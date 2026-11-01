"""Authenticated viewing and bounded device video ingress; model work is elsewhere."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading

from fastapi import Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .auth import digest
from .contracts import VideoView
from .runtime import DomainError
from .video import unpack_video

logger = logging.getLogger(__name__)


def install_video_routes(app, service, admin):
    live_slots = threading.BoundedSemaphore(3)
    uplink_slot = threading.BoundedSemaphore(1)

    @app.post("/api/v2/video/viewers", dependencies=[Depends(admin)])
    def view(body: VideoView, request: Request, s=Depends(service)):
        owner = digest(request.cookies.get("sss_session", ""))
        return s.video.renew(owner, body.lease_id, body.enrollment_session_id)

    @app.delete("/api/v2/video/viewers/{lease_id}", status_code=204, dependencies=[Depends(admin)])
    def release_view(lease_id: str, request: Request, s=Depends(service)):
        s.video.release(digest(request.cookies.get("sss_session", "")), lease_id)

    @app.get("/api/v2/live.mjpg", dependencies=[Depends(admin)])
    async def camera_stream(request: Request, lease_id: str, s=Depends(service)):
        owner = digest(request.cookies.get("sss_session", ""))
        with s.lock:
            if not s.video.authorized(owner, lease_id):
                raise DomainError("VIDEO_VIEW_EXPIRED", 404)
        if not live_slots.acquire(blocking=False):
            raise DomainError("LIVE_VIEW_LIMIT", 429)
        event, loop = asyncio.Event(), asyncio.get_running_loop()

        async def frames():
            previous, next_auth = None, 0
            try:
                with s.lock:
                    s.video.listeners[event] = loop
                while not s.stopping:
                    if s.now() >= next_auth:
                        if await run_in_threadpool(request.app.state.auth.session, owner) is None:
                            s.video.release(owner)
                            return
                        next_auth = s.now() + 1000
                    event.clear()
                    with s.lock:
                        if not s.video.authorized(owner, lease_id):
                            return
                        frame = s.video.latest
                        age = (
                            (s.now() - frame["received_ms"] + frame.get("capture_age_ms", 0))
                            if frame
                            else 100000
                        )
                        fresh = bool(
                            frame
                            and age <= s.cfg["video"]["stale_ms"]
                            and frame["frame_id"] != previous
                        )
                    if fresh:
                        previous = frame["frame_id"]
                        yield (
                            b"--frame\r\nContent-Type: image/jpeg\r\n"
                            + f"Content-Length: {len(frame['jpeg'])}\r\n".encode()
                            + f"X-Frame-Id: {previous}\r\nX-Frame-Age-Ms: {max(0, age)}\r\n".encode()
                            + f"X-Video-Revision: {frame['video_revision']}\r\n".encode()
                            + f"X-Mode-Revision: {frame['mode_revision']}\r\n\r\n".encode()
                            + frame["jpeg"]
                            + b"\r\n"
                        )
                        continue
                    try:
                        await asyncio.wait_for(event.wait(), timeout=1)
                    except TimeoutError:
                        pass
            finally:
                with s.lock:
                    s.video.listeners.pop(event, None)
                live_slots.release()

        # WebKit rejects multipart/x-mixed-replace in fetch(). The JS reader parses
        # the same bounded envelope itself; keep native MJPEG for existing clients.
        media = (
            "application/octet-stream"
            if request.headers.get("accept") == "application/octet-stream"
            else "multipart/x-mixed-replace; boundary=frame"
        )
        return StreamingResponse(
            frames(),
            media_type=media,
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
                "Vary": "Accept",
            },
        )

    @app.websocket("/api/v2/devices/{device_id}/video")
    async def video_uplink(socket: WebSocket):
        s = socket.app.state.service
        acquired = False
        started = last_received = s.now()
        accepted_frames = 0
        close_code, close_reason = 1000, "DEVICE_DISCONNECTED"
        try:
            socket.app.state.auth.device(socket)
            # Device channel has no browser-cookie authority and accepts no browser Origin.
            if socket.headers.get("origin"):
                raise DomainError("DEVICE_ORIGIN_FORBIDDEN", 403)
            acquired = uplink_slot.acquire(blocking=False)
            if not acquired:
                await socket.close(code=1013)
                return
            await socket.accept()
            await socket.send_json({"protocol_version": 1, "window_frames": 2})
            while not s.stopping:
                async with asyncio.timeout(2):
                    message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    close_code = message.get("code", 1006)
                    return
                data = message.get("bytes")
                if data is None:
                    raise DomainError("VIDEO_BINARY_REQUIRED", 415)
                meta, jpeg = unpack_video(data)
                result = await run_in_threadpool(s.accept_video, meta, jpeg, s.now())
                accepted_frames += 1
                last_received = s.now()
                async with asyncio.timeout(1):
                    await socket.send_json(result)
        except DomainError as exc:
            # Do not echo metadata/token or partially parsed payload.
            close_code, close_reason = (1009 if exc.status == 413 else 1008), exc.code
            await socket.close(code=close_code, reason=close_reason)
        except TimeoutError:
            close_code, close_reason = 1008, "VIDEO_TIMEOUT"
            await socket.close(code=close_code, reason=close_reason)
        except (sqlite3.Error, OSError):
            close_code, close_reason = 1011, "STORAGE_FAULT"
            with s.lock:
                s.fail_closed("STORAGE_FAULT")
            await socket.close(code=close_code, reason=close_reason)
        except WebSocketDisconnect as exc:
            close_code = exc.code
        finally:
            if acquired:
                uplink_slot.release()
                if close_code not in (1000, 1001):
                    # Transport metadata only: never log peer input, session, token or JPEG.
                    now = s.now()
                    with s.lock:
                        fps = s.video.metrics()["received_fps"]
                        ai_p95 = s.analysis_metrics(now)["duration_p95_ms"]
                        telemetry = dict(s.device_telemetry or {})
                    logger.warning(
                        "VIDEO_UPLINK_END reason=%s code=%s accepted_frames=%d "
                        "duration_ms=%d idle_ms=%d received_fps=%s ai_p95_ms=%s "
                        "control_rtt_ms=%s jpeg_bytes=%s free_heap_bytes=%s "
                        "video_reconnects=%s",
                        close_reason,
                        close_code,
                        accepted_frames,
                        max(0, now - started),
                        max(0, now - last_received),
                        fps,
                        ai_p95,
                        telemetry.get("control_rtt_ms"),
                        telemetry.get("last_jpeg_bytes"),
                        telemetry.get("free_heap_bytes"),
                        telemetry.get("video_reconnects"),
                    )
