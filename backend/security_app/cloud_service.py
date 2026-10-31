"""Independent cloud application: cloud inference, durable event analytics and a web view.

Deployment is opt-in. Cloud has no alarm GPIO or local ESP32 credentials.
No JPEG or face embeddings are persisted by the cloud service.
"""

from __future__ import annotations

import base64
import html
import io
import os
import secrets
import sqlite3
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

from .config import load_config
from .runtime import clock_ms


class CloudEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    device_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    reason: str = Field(min_length=1, max_length=80)
    severity: Literal["alarm", "warning", "info"]
    occurred_at: datetime
    source: Literal["hardware", "simulator"]


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["monitoring", "enrollment"]
    jpeg: str = Field(min_length=1, max_length=750_000)
    gallery: dict[str, list[str]] = Field(default_factory=dict)


def init_event_database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS cloud_events (
          event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
          reason TEXT NOT NULL, severity TEXT NOT NULL,
          occurred_at TEXT NOT NULL, source TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS cloud_event_time ON cloud_events(occurred_at)")


def create_cloud_app(*, db_path=None, token=None, vision_factory=None) -> FastAPI:
    api_token = token or os.environ.get("SSS_CLOUD_API_TOKEN", "")
    if len(api_token) < 32:
        raise ValueError("SSS_CLOUD_API_TOKEN must be >=32 chars")
    database_path = Path(db_path or os.environ.get("SSS_CLOUD_DB", "runtime/cloud/events.sqlite3"))
    admin_user = os.environ.get("SSS_CLOUD_VIEW_USER", "")
    admin_password = os.environ.get("SSS_CLOUD_VIEW_PASSWORD", "")
    basic = HTTPBasic(auto_error=False)

    @asynccontextmanager
    async def lifespan(app):
        init_event_database(database_path)
        app.state.vision = None
        app.state.vision_error = None
        app.state.vision_lock = threading.Lock()
        try:
            if vision_factory:
                app.state.vision = vision_factory()
            else:
                from .vision import OpenCVVision

                app.state.vision = OpenCVVision(load_config())
        except Exception:
            app.state.vision_error = "CLOUD_MODEL_INIT_FAILED"
        yield

    app = FastAPI(title="Smart Security — Cloud Analytics", version="3.0.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_payload(_request, _exc):
        # Pydantic's default validation errors may include the rejected base64 input.
        # Never echo biometric image or gallery data back to the caller.
        return JSONResponse({"error": "INVALID_PAYLOAD"}, status_code=422)

    @app.middleware("http")
    async def secure_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
        )
        return response

    def authenticate(request: Request):
        supplied = request.headers.get("authorization", "")
        expected = f"Bearer {api_token}"
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, "CLOUD_AUTH_REQUIRED")

    def admin(credentials: HTTPBasicCredentials | None = Depends(basic)):
        if (
            not admin_user
            or not admin_password
            or credentials is None
            or not secrets.compare_digest(credentials.username, admin_user)
            or not secrets.compare_digest(credentials.password, admin_password)
        ):
            raise HTTPException(
                401, "CLOUD_VIEW_AUTH_REQUIRED", headers={"WWW-Authenticate": "Basic"}
            )

    @app.get("/health/live")
    def live():
        return {"alive": True}

    @app.get("/health/ready", dependencies=[Depends(authenticate)])
    def ready(request: Request):
        return {
            "ready": request.app.state.vision is not None,
            "vision_error": request.app.state.vision_error,
        }

    @app.post("/v1/reset", dependencies=[Depends(authenticate)])
    def reset(request: Request):
        with request.app.state.vision_lock:
            vision = request.app.state.vision
            if vision is None:
                raise HTTPException(503, "VISION_UNAVAILABLE")
            vision.reset()
        return {"ok": True}

    @app.post("/v1/infer", dependencies=[Depends(authenticate)])
    def infer(body: InferenceRequest, request: Request):
        try:
            jpeg = base64.b64decode(body.jpeg, validate=True)
        except ValueError as exc:
            raise HTTPException(422, "BAD_JPEG_ENCODING") from exc
        if len(jpeg) > 524288 or len(jpeg) < 64:
            raise HTTPException(413, "BAD_FRAME_SIZE")
        # A small compressed JPEG can still decode to a massive image.
        try:
            with Image.open(io.BytesIO(jpeg)) as header:
                width, height = header.size
                if header.format != "JPEG" or not (1 <= width <= 1600 and 1 <= height <= 1200):
                    raise HTTPException(422, "BAD_FRAME_DIMENSIONS")
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise HTTPException(422, "INVALID_JPEG") from exc
        if len(body.gallery) > 20 or sum(len(x) for x in body.gallery.values()) > 200:
            raise HTTPException(413, "GALLERY_TOO_LARGE")
        vectors = {}
        try:
            for person_id, encoded in body.gallery.items():
                if len(person_id) > 64:
                    raise ValueError("PERSON_ID_TOO_LONG")
                vectors[person_id] = []
                for item in encoded:
                    if len(item) > 1024:
                        raise ValueError("INVALID_VECTOR_LENGTH")
                    vector = np.frombuffer(
                        base64.b64decode(item, validate=True), dtype="<f4"
                    ).copy()
                    if vector.size != 128 or not np.isfinite(vector).all():
                        raise ValueError("INVALID_VECTOR")
                    vectors[person_id].append(vector)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "BAD_GALLERY") from exc
        with request.app.state.vision_lock:
            vision = request.app.state.vision
            if vision is None:
                raise HTTPException(503, "VISION_UNAVAILABLE")
            try:
                if body.purpose == "enrollment":
                    vector, quality = vision.enrollment_sample(jpeg)
                    output = {
                        "feature": base64.b64encode(
                            np.asarray(vector, dtype="<f4").tobytes()
                        ).decode()
                        if vector is not None
                        else None,
                        "quality": quality,
                    }
                else:
                    observations = vision.analyze(jpeg, vectors, clock_ms())
                    output = {"observations": [item.model_dump() for item in observations]}
                output["environment"] = getattr(vision, "last_environment", None)
            except (ValueError, cv2.error) as exc:
                raise HTTPException(422, "VISION_INPUT_INVALID") from exc
        return output

    @app.post("/v1/events", dependencies=[Depends(authenticate)], status_code=201)
    def event(body: CloudEvent):
        with sqlite3.connect(database_path) as db:
            db.execute(
                "INSERT OR IGNORE INTO cloud_events VALUES(?,?,?,?,?,?)",
                (
                    body.event_id,
                    body.device_id,
                    body.reason,
                    body.severity,
                    body.occurred_at.astimezone(timezone.utc).isoformat(),
                    body.source,
                ),
            )
        return {"event_id": body.event_id}

    @app.get("/v1/summary", dependencies=[Depends(admin)])
    def summary():
        with sqlite3.connect(database_path) as db:
            by_severity = dict(
                db.execute("SELECT severity,count(*) FROM cloud_events GROUP BY severity")
            )
            by_reason = dict(db.execute("SELECT reason,count(*) FROM cloud_events GROUP BY reason"))
            by_day = [
                dict(day=row[0], count=row[1])
                for row in db.execute(
                    "SELECT substr(occurred_at,1,10),count(*) FROM cloud_events "
                    "GROUP BY substr(occurred_at,1,10) ORDER BY 1 DESC LIMIT 14"
                )
            ]
            total = db.execute("SELECT count(*) FROM cloud_events").fetchone()[0]
        return {
            "total_events": total,
            "severity": by_severity,
            "reasons": by_reason,
            "daily": by_day,
            "analysis_location": "cloud",
            "evidence_images_stored": False,
        }

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(admin)])
    def dashboard():
        report = summary()

        def rows(values):
            return "".join(
                f"<tr><th>{html.escape(str(k))}</th><td>{v}</td></tr>" for k, v in values
            )

        return (
            "<!doctype html><html lang='vi'><head><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>Smart Security Cloud</title>"
            "<style>body{font:16px system-ui;background:#101827;color:#edf7fa;max-width:750px;"
            "margin:auto;padding:1.5rem}section{background:#1d3040;padding:1rem;border-radius:14px;"
            "margin:1rem 0}table{width:100%;text-align:left;border-collapse:collapse}"
            "td,th{border-bottom:1px solid #455b67;padding:.7rem}</style></head><body>"
            "<h1>Smart Security · Cloud analytics</h1>"
            "<p>Thống kê sự kiện đã đồng bộ từ camera; không lưu ảnh/khuôn mặt trên cloud.</p>"
            f"<section><h2>{report['total_events']} sự kiện</h2></section>"
            f"<section><h2>Theo mức cảnh báo</h2><table>{rows(report['severity'].items())}</table></section>"
            f"<section><h2>Theo loại sự kiện</h2><table>{rows(report['reasons'].items())}</table></section>"
            f"<section><h2>14 ngày gần nhất có dữ liệu</h2><table>{rows((x['day'], x['count']) for x in report['daily'])}</table></section>"
            "</body></html>"
        )

    return app


class _LazyApp:
    """Defers required env validation until an actual ASGI server loads the app."""

    def __init__(self):
        self.app = None

    async def __call__(self, scope, receive, send):
        if self.app is None:
            self.app = create_cloud_app()
        await self.app(scope, receive, send)


app = _LazyApp()
