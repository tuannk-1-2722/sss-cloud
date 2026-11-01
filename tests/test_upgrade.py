"""Isolated checks for PWA, image gating, cloud security and idempotent analytics."""

from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from security_app.cloud_remote import cloud_credentials
from security_app.cloud_service import create_cloud_app
from security_app.main import create_app
from security_app.notifications import dashboard_link, telegram_message
from security_app.vision import environment_quality


def test_scene_quality_is_conservative(cfg):
    dark = np.zeros((480, 640, 3), np.uint8)
    bright = np.full((480, 640, 3), 255, np.uint8)
    normal = np.full((480, 640, 3), 125, np.uint8)
    assert environment_quality(dark, cfg)["reason"] == "SCENE_TOO_DARK"
    assert environment_quality(bright, cfg)["reason"] == "SCENE_EXPOSURE_FAILURE"
    assert environment_quality(normal, cfg)["usable"]


def test_telegram_link_must_be_https(monkeypatch):
    monkeypatch.setenv("SSS_DASHBOARD_URL", "http://127.0.0.1:8000")
    assert dashboard_link() is None
    monkeypatch.setenv("SSS_DASHBOARD_URL", "https://private.tailnet.ts.net")
    assert dashboard_link() == "https://private.tailnet.ts.net/"
    text, keyboard = telegram_message(
        {
            "event_reason": "unresolved_presence",
            "created_at": "2026-10-20T01:00:00+00:00",
            "event_id": "00000000-0000-0000-0000-000000000001",
        }
    )
    assert "chưa đủ căn cứ" in text
    assert keyboard["inline_keyboard"][0][0]["url"] == "https://private.tailnet.ts.net/?live=1"


def test_pwa_shell_available_without_admin_but_camera_not(cfg):
    with TestClient(create_app(cfg)) as client:
        manifest = client.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["display"] == "standalone"
        assert client.get("/sw.js").status_code == 200
        assert client.get("/static/icon-192.png").status_code == 200
        assert client.get("/api/v2/live.mjpg").status_code == 401
        sw = client.get("/sw.js").text
        assert "/api/v2/" not in sw and "ASSETS" in sw


def test_cloud_credentials_require_https_and_token(monkeypatch):
    monkeypatch.setenv("SSS_CLOUD_API_URL", "http://cloud.example.org")
    monkeypatch.setenv("SSS_CLOUD_API_TOKEN", "X" * 32)
    with pytest.raises(ValueError):
        cloud_credentials()
    monkeypatch.setenv("SSS_CLOUD_API_URL", "https://cloud.example.org")
    assert cloud_credentials()[0] == "https://cloud.example.org"


class FakeVision:
    last_environment = {"usable": False, "reason": "SCENE_TOO_DARK"}

    def reset(self):
        return None

    def enrollment_sample(self, jpeg):
        return None, {"reason": "SCENE_TOO_DARK"}

    def analyze(self, jpeg, gallery, now):
        return []


def test_cloud_analytics_auth_storage_and_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("SSS_CLOUD_VIEW_USER", "viewer")
    monkeypatch.setenv("SSS_CLOUD_VIEW_PASSWORD", "sample-view-password")
    token = "a" * 32
    with TestClient(
        create_cloud_app(
            db_path=tmp_path / "cloud.sqlite3",
            token=token,
            vision_factory=FakeVision,
        )
    ) as client:
        assert client.get("/v1/summary").status_code == 401
        assert client.get("/health/ready").status_code == 401
        assert client.get("/health/ready", headers={"Authorization": f"Bearer {token}"}).json()[
            "ready"
        ]
        event = {
            "event_id": "00000000-0000-0000-0000-000000000001",
            "device_id": "camera-1",
            "reason": "unresolved_presence",
            "severity": "warning",
            "source": "hardware",
            "occurred_at": "2026-10-20T01:00:00+00:00",
        }
        for _ in range(2):
            assert (
                client.post(
                    "/v1/events", headers={"Authorization": f"Bearer {token}"}, json=event
                ).status_code
                == 201
            )
        report = client.get("/v1/summary", auth=("viewer", "sample-view-password")).json()
        assert report["total_events"] == 1
        assert report["severity"] == {"warning": 1}
        assert report["evidence_images_stored"] is False
        html = client.get("/", auth=("viewer", "sample-view-password"))
        assert html.status_code == 200 and "Cloud analytics" in html.text
        assert not any(tmp_path.glob("*.jpg"))
        small_jpeg = cv2.imencode(".jpg", np.full((16, 16, 3), 100, np.uint8))[1].tobytes()
        result = client.post(
            "/v1/infer",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "purpose": "enrollment",
                "jpeg": base64.b64encode(small_jpeg).decode(),
                "gallery": {},
            },
        )
        assert result.status_code == 200
        assert result.json()["feature"] is None
        assert result.json()["environment"]["reason"] == "SCENE_TOO_DARK"
        vector = np.eye(1, 512, dtype=np.float32).reshape(-1)
        monitoring = {
            "purpose": "monitoring",
            "jpeg": base64.b64encode(small_jpeg).decode(),
            "gallery": {"person-1": [base64.b64encode(vector.tobytes()).decode()]},
        }
        response = client.post(
            "/v1/infer",
            headers={"Authorization": "******"},
            json=monitoring,
        )
        assert response.status_code == 200
        monitoring["gallery"]["person-1"] = [
            base64.b64encode(np.eye(1, 128, dtype=np.float32).tobytes()).decode()
        ]
        response = client.post(
            "/v1/infer",
            headers={"Authorization": "******"},
            json=monitoring,
        )
        assert response.status_code == 422


def test_cloud_bridge_is_metadata_only_and_marks_outbox_sent(service, monkeypatch):
    from datetime import datetime, timezone
    from uuid import uuid4

    from security_app.cloud_bridge import CloudBridge

    event_id = str(uuid4())
    with service.store.connection() as db:
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,NULL)",
            (
                event_id,
                str(uuid4()),
                "unresolved_presence",
                "warning",
                datetime.now(timezone.utc).isoformat(),
                "simulator",
                "{}",
                None,
            ),
        )
        db.execute("INSERT INTO cloud_outbox(event_id) VALUES(?)", (event_id,))
    service.cfg["cloud"]["events_enabled"] = False
    bridge = CloudBridge(service)
    bridge.url, bridge.token = "https://cloud.example.org", "x" * 32
    seen = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, headers, json):
            seen.append(json)
            return type("Response", (), {"status_code": 201})()

    monkeypatch.setattr("security_app.cloud_bridge.httpx.Client", FakeClient)
    bridge.step()
    bridge.step()
    assert len(seen) == 1
    assert seen[0]["event_id"] == event_id
    assert "jpeg" not in seen[0] and "person_id" not in seen[0]
    with service.store.connection() as db:
        assert (
            db.execute("SELECT state FROM cloud_outbox WHERE event_id=?", (event_id,)).fetchone()[0]
            == "sent"
        )
