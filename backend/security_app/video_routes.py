"""Authenticated viewing and bounded device video ingress; model work is elsewhere."""

from __future__ import annotations

import asyncio
import sqlite3
import threading

from fastapi import Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .auth import digest
from .contracts import VideoView
from .runtime import DomainError
from .video import unpack_video


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

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.websocket("/api/v2/devices/{device_id}/video")
    async def video_uplink(socket: WebSocket):
        s = socket.app.state.service
        acquired = False
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
                    return
                data = message.get("bytes")
                if data is None:
                    raise DomainError("VIDEO_BINARY_REQUIRED", 415)
                meta, jpeg = unpack_video(data)
                result = await run_in_threadpool(s.accept_video, meta, jpeg, s.now())
                async with asyncio.timeout(1):
                    await socket.send_json(result)
        except DomainError as exc:
            # Do not echo metadata/token or partially parsed payload.
            await socket.close(code=1009 if exc.status == 413 else 1008, reason=exc.code)
        except TimeoutError:
            await socket.close(code=1008, reason="VIDEO_TIMEOUT")
        except (sqlite3.Error, OSError):
            with s.lock:
                s.fail_closed("STORAGE_FAULT")
            await socket.close(code=1011, reason="STORAGE_FAULT")
        except WebSocketDisconnect:
            pass
        finally:
            if acquired:
                uplink_slot.release()
