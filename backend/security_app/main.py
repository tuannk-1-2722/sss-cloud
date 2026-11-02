from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .auth import Auth, digest
from .config import load_config
from .contracts import (
    FrameMeta,
    Hello,
    IdempotentAction,
    Login,
    ModeChange,
    NotificationTest,
    PersonCreate,
    PushSubscription,
    PushUnsubscribe,
    Silence,
    Sync,
    SyntheticFrame,
)
from .push import (
    add_subscription,
    push_status,
    remove_subscription,
    valid_endpoint,
    valid_key,
)
from .service import DomainError, SecurityService

PACKAGE = Path(__file__).parent


class BoundedRequests:
    """Buffer at most one bounded body before FastAPI parses JSON or JPEG."""

    def __init__(self, app, max_frame_bytes: int):
        self.app = app
        self.max_frame_bytes = max_frame_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        limit = self.max_frame_bytes if scope["path"].endswith("/frames") else 32768
        body = bytearray()
        try:
            async with asyncio.timeout(2):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > limit:
                        raise DomainError("BODY_TOO_LARGE", 413)
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except (DomainError, TimeoutError) as exc:
            code, status = (
                (exc.code, exc.status) if isinstance(exc, DomainError) else ("UPLOAD_TOO_SLOW", 408)
            )
            response = JSONResponse(
                {"error": {"code": code}},
                status_code=status,
                headers={"Connection": "close", "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


def create_app(config: dict | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        service = SecurityService(config or load_config())
        app.state.service = service
        app.state.auth = Auth(service)
        from .notifications import Notifier

        notifier = Notifier(service)
        from .push import MediaSigner, PushNotifier

        app.state.media_signer = MediaSigner(service.runtime)
        push_notifier = PushNotifier(service)
        cloud_bridge = None
        try:
            from .cloud_bridge import CloudBridge

            cloud_bridge = CloudBridge(service)
            yield
        finally:
            notifier.close()
            push_notifier.close()
            if cloud_bridge is not None:
                cloud_bridge.close()
            service.close()

    app = FastAPI(title="Smart Security System", version="2.2.0", lifespan=lifespan)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            {"error": {"code": exc.code}},
            status_code=exc.status,
            headers={"Retry-After": "2"} if exc.status in {429, 503} else None,
        )

    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        s = request.app.state.service
        # Endpoint may have failed outside its mutation path (e.g. history/media reads).
        with s.lock:
            s.fail_closed("STORAGE_FAULT")
        return JSONResponse(
            {"error": {"code": "STORAGE_FAULT"}}, status_code=503, headers={"Retry-After": "2"}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo body/password/embedding in validation errors.
        return JSONResponse({"error": {"code": "INVALID_PAYLOAD"}}, status_code=422)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    def service(request: Request) -> SecurityService:
        return request.app.state.service

    def admin(request: Request):
        return request.app.state.auth.admin(request)

    def device(request: Request):
        request.app.state.auth.device(request)

    async def bounded_body(request: Request, limit: int) -> bytes:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise DomainError("BODY_TOO_LARGE", 413)
        return bytes(data)

    app.add_middleware(
        BoundedRequests, max_frame_bytes=(config or load_config())["limits"]["max_frame_bytes"]
    )

    @app.get("/health/live")
    def live():
        return {"alive": True}

    @app.get("/health/ready")
    def ready(s: SecurityService = Depends(service)):
        ok = s.vision_ready and s.thread.is_alive() and s.worker_error is None
        return JSONResponse({"ready": ok}, status_code=200 if ok else 503)

    from .video_routes import install_video_routes

    install_video_routes(app, service, admin)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def pwa_manifest():
        return Response(
            (PACKAGE / "static/manifest.webmanifest").read_bytes(),
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    def pwa_worker():
        return Response(
            (PACKAGE / "static/sw.js").read_bytes(),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        return (PACKAGE / "templates/dashboard.html").read_text(encoding="utf-8")

    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.post("/api/v2/auth/login")
    def login(body: Login, request: Request, s: SecurityService = Depends(service)):
        token, csrf = request.app.state.auth.login(
            body.username, body.password, request.client.host if request.client else "local"
        )
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie(
            "sss_session",
            token,
            max_age=28800,
            httponly=True,
            samesite="strict",
            secure=os.environ.get("SSS_HTTPS") == "1",
        )
        return response

    @app.post("/api/v2/auth/logout", dependencies=[Depends(admin)])
    def logout(request: Request, s: SecurityService = Depends(service)):
        s.video.release(digest(request.cookies.get("sss_session", "")))
        with s.store.connection() as db:
            db.execute(
                "DELETE FROM web_sessions WHERE token_hash=?",
                (digest(request.cookies.get("sss_session", "")),),
            )
        response = Response(status_code=204)
        response.delete_cookie("sss_session")
        return response

    @app.get("/api/v2/status", dependencies=[Depends(admin)])
    def status(s: SecurityService = Depends(service)):
        return s.status()

    @app.get("/api/v2/diagnostics/recognition", dependencies=[Depends(admin)])
    def recognition_diagnostics(after: int = 0, s: SecurityService = Depends(service)):
        with s.lock:
            return s.diagnostics.snapshot(s.now(), after=max(0, after))

    @app.post("/api/v2/notifications/test", status_code=202, dependencies=[Depends(admin)])
    def notification_test(body: NotificationTest, s: SecurityService = Depends(service)):
        from .notifications import enqueue_test

        with s.lock:
            return enqueue_test(s, body.idempotency_key, body.include_photo)

    @app.post("/api/v2/diagnostics/recognition/start", dependencies=[Depends(admin)])
    def start_recognition_diagnostics(s: SecurityService = Depends(service)):
        with s.lock:
            return s.diagnostics.start(s.now())

    @app.delete("/api/v2/diagnostics/recognition", status_code=204, dependencies=[Depends(admin)])
    def stop_recognition_diagnostics(s: SecurityService = Depends(service)):
        with s.lock:
            s.diagnostics.stop()
        return Response(status_code=204)

    @app.put("/api/v2/system/mode", dependencies=[Depends(admin)])
    def mode(body: ModeChange, s: SecurityService = Depends(service)):
        return s.set_mode(body.mode, body.expected_revision)

    @app.post("/api/v2/system/silence", status_code=202, dependencies=[Depends(admin)])
    def silence(body: Silence, s: SecurityService = Depends(service)):
        return s.action(body.idempotency_key, "silence", body.visit_id)

    @app.post("/api/v2/system/test-alarm", status_code=202, dependencies=[Depends(admin)])
    def test_alarm(body: IdempotentAction, s: SecurityService = Depends(service)):
        return s.action(body.idempotency_key, "test")

    @app.post("/api/v2/capture/preview", status_code=201, dependencies=[Depends(admin)])
    def preview(s: SecurityService = Depends(service)):
        raise DomainError("PREVIEW_RETIRED_USE_VIDEO_VIEW", 410)

    @app.get("/api/v2/capture", dependencies=[Depends(admin)])
    def capture(s: SecurityService = Depends(service)):
        return s.capture_status()

    @app.delete("/api/v2/capture", status_code=204, dependencies=[Depends(admin)])
    def cancel_capture(s: SecurityService = Depends(service)):
        with s.lock:
            s._disarm("capture_cancelled")

    @app.get("/api/v2/people", dependencies=[Depends(admin)])
    def people(s: SecurityService = Depends(service)):
        return s.store.people()

    @app.post("/api/v2/people", status_code=201, dependencies=[Depends(admin)])
    def person(body: PersonCreate, s: SecurityService = Depends(service)):
        return s.create_person(body.display_name)

    @app.post("/api/v2/people/{person_id}/disable", dependencies=[Depends(admin)])
    def disable(person_id: str, s: SecurityService = Depends(service)):
        s.deactivate_person(person_id)
        return {"active": False}

    @app.post("/api/v2/people/{person_id}/activate", dependencies=[Depends(admin)])
    def activate(person_id: str, s: SecurityService = Depends(service)):
        s.activate_person(person_id)
        return {"active": True}

    @app.delete("/api/v2/people/{person_id}", status_code=204, dependencies=[Depends(admin)])
    def delete(person_id: str, s: SecurityService = Depends(service)):
        s.deactivate_person(person_id, delete=True)

    @app.post(
        "/api/v2/people/{person_id}/enrollment", status_code=201, dependencies=[Depends(admin)]
    )
    def enroll(person_id: str, s: SecurityService = Depends(service)):
        return s.start_capture("enrollment", person_id)

    @app.post("/api/v2/enrollments/{session_id}/next-round", dependencies=[Depends(admin)])
    def next_round(session_id: str, s: SecurityService = Depends(service)):
        return s.next_enrollment_round(session_id)

    @app.post("/api/v2/enrollments/{session_id}/commit", dependencies=[Depends(admin)])
    def commit(session_id: str, body: ModeChange, s: SecurityService = Depends(service)):
        if body.mode != "DISARMED":
            raise DomainError("COMMIT_RETURNS_DISARMED", 422)
        return s.commit_enrollment(session_id, body.expected_revision)

    @app.get("/api/v2/events", dependencies=[Depends(admin)])
    def events(limit: int = 50, s: SecurityService = Depends(service)):
        if not 1 <= limit <= 100:
            raise DomainError("INVALID_LIMIT", 422)
        return s.store.event_rows(limit)

    @app.post("/api/v2/events/{event_id}/acknowledge", dependencies=[Depends(admin)])
    def acknowledge(event_id: str, s: SecurityService = Depends(service)):
        s.acknowledge(event_id)
        return {"acknowledged": True}

    @app.get("/api/v2/media/{media_id}", dependencies=[Depends(admin)])
    def media(media_id: str, s: SecurityService = Depends(service)):
        with s.lock, s.store.connection() as db:
            if not db.execute("SELECT 1 FROM events WHERE media_id=?", (media_id,)).fetchone():
                raise DomainError("MEDIA_NOT_FOUND", 404)
            path = s.runtime / "media" / f"{media_id}.jpg"
            if not path.is_file():
                raise DomainError("MEDIA_NOT_FOUND", 404)
            return Response(path.read_bytes(), media_type="image/jpeg")

    @app.get("/api/v2/push/config", dependencies=[Depends(admin)])
    def push_config(s: SecurityService = Depends(service)):
        return push_status(s.runtime, s.cfg)

    @app.post("/api/v2/push/subscribe", status_code=201, dependencies=[Depends(admin)])
    def push_subscribe(body: PushSubscription, s: SecurityService = Depends(service)):
        status = push_status(s.runtime, s.cfg)
        if not (status["enabled"] and status["configured"]):
            raise DomainError("PUSH_DISABLED", 409)
        if not (
            valid_endpoint(body.endpoint)
            and valid_key(body.keys.p256dh, 65)
            and valid_key(body.keys.auth, 16)
        ):
            raise DomainError("PUSH_SUBSCRIPTION_INVALID", 422)
        if not add_subscription(s.store, body.endpoint, body.keys.p256dh, body.keys.auth):
            raise DomainError("PUSH_SUBSCRIPTION_LIMIT", 409)
        return {"subscribed": True}

    @app.post("/api/v2/push/unsubscribe", status_code=204, dependencies=[Depends(admin)])
    def push_unsubscribe(body: PushUnsubscribe, s: SecurityService = Depends(service)):
        remove_subscription(s.store, body.endpoint)
        return Response(status_code=204)

    @app.get("/push-media/{media_id}", include_in_schema=False)
    def push_media(
        media_id: str, exp: int, sig: str, request: Request, s: SecurityService = Depends(service)
    ):
        # Capability link for the OS notification image fetch: no cookie, HMAC + expiry only.
        # Every failure is the same 404 so the endpoint reveals nothing about media ids.
        signer = request.app.state.media_signer
        if (
            not s.cfg["notifications"]["push_enabled"]
            or not re.fullmatch(r"[0-9a-fA-F-]{32,40}", media_id)
            or not signer.verify(media_id, exp, sig)
        ):
            raise DomainError("MEDIA_NOT_FOUND", 404)
        with s.store.connection() as db:
            known = db.execute("SELECT 1 FROM events WHERE media_id=?", (media_id,)).fetchone()
        path = s.runtime / "media" / f"{media_id}.jpg"
        if not known or not path.is_file():
            raise DomainError("MEDIA_NOT_FOUND", 404)
        return Response(path.read_bytes(), media_type="image/jpeg")

    @app.get("/api/v2/latest-frame", dependencies=[Depends(admin)])
    def latest_frame(s: SecurityService = Depends(service)):
        with s.lock:
            if (
                not s.latest
                or s.now() - s.latest["received_ms"] + s.latest.get("capture_age_ms", 0)
                > s.cfg["video"]["stale_ms"]
            ):
                raise DomainError("FRAME_NOT_FOUND", 404)
            return Response(
                s.latest["jpeg"],
                media_type="image/jpeg",
                headers={
                    "X-Frame-Id": s.latest["frame_id"],
                    "X-Observed-Ms": str(
                        s.latest["received_ms"] - s.latest.get("capture_age_ms", 0)
                    ),
                    "X-Received-Ms": str(s.latest["received_ms"]),
                },
            )

    @app.get("/api/v2/commands", dependencies=[Depends(admin)])
    def commands(s: SecurityService = Depends(service)):
        with s.store.connection() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM commands ORDER BY created_at DESC LIMIT 50")
            ]

    @app.post("/api/v2/devices/{device_id}/hello", dependencies=[Depends(device)])
    def hello(body: Hello, request: Request, s: SecurityService = Depends(service)):
        request.app.state.auth.hello_rate_limit()
        return s.hello(body)

    @app.post("/api/v2/devices/{device_id}/sync", dependencies=[Depends(device)])
    def sync(body: Sync, s: SecurityService = Depends(service)):
        return s.sync(body)

    @app.post(
        "/api/v2/devices/{device_id}/synthetic-frames",
        status_code=202,
        dependencies=[Depends(device)],
    )
    def synthetic(body: SyntheticFrame, s: SecurityService = Depends(service)):
        result = s.accept(body.metadata, observations=body.observations)
        return JSONResponse(result, status_code=200 if result["duplicate"] else 202)

    @app.post("/api/v2/devices/{device_id}/frames", status_code=202, dependencies=[Depends(device)])
    async def frames(request: Request, s: SecurityService = Depends(service)):
        # Raw JPEG + small JSON header is simpler on ESP32 than constructing multipart bodies.
        if request.headers.get("content-type", "").split(";")[0] != "image/jpeg":
            raise DomainError("JPEG_REQUIRED", 415)
        metadata = request.headers.get("x-frame-meta", "")
        if len(metadata) > 4096:
            raise DomainError("METADATA_TOO_LARGE", 413)
        try:
            meta = FrameMeta.model_validate_json(metadata)
        except ValidationError as exc:
            raise DomainError("INVALID_METADATA", 422) from exc
        try:
            async with asyncio.timeout(2):
                jpeg = await bounded_body(request, s.cfg["limits"]["max_frame_bytes"])
        except TimeoutError as exc:
            raise DomainError("UPLOAD_TOO_SLOW", 408) from exc
        result = await run_in_threadpool(s.accept, meta, jpeg)
        return JSONResponse(result, status_code=200 if result["duplicate"] else 202)

    return app


app = create_app()
