import json

import pytest
from fastapi.testclient import TestClient
from security_app.main import create_app


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg)) as c:
        yield c


def login(client):
    response = client.post(
        "/api/v2/auth/login", json={"username": "admin", "password": "test-password-private"}
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_auth_roles_and_csrf(client):
    assert client.get("/api/v2/status").status_code == 401
    assert client.get("/api/v2/media/not-a-media").status_code == 401
    login(client)
    assert (
        client.put(
            "/api/v2/system/mode", json={"mode": "DISARMED", "expected_revision": 1}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v2/devices/camera-1/hello", json={}, headers={"Authorization": "Bearer fake"}
        ).status_code
        == 401
    )


def test_empty_gallery_cannot_arm(client):
    headers = login(client)
    response = client.put(
        "/api/v2/system/mode", json={"mode": "ARMED", "expected_revision": 1}, headers=headers
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "EMPTY_GALLERY"


def test_validation_never_echoes_password(client):
    password = "SECRET-WOULD-LEAK" * 100
    response = client.post("/api/v2/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 422 and "SECRET-WOULD-LEAK" not in response.text


def test_device_does_not_have_admin_access(client):
    response = client.get(
        "/api/v2/people", headers={"Authorization": "Bearer device-test-token-private"}
    )
    assert response.status_code == 401


def test_invalid_jpeg_and_body_limit(client):
    token = {"Authorization": "Bearer device-test-token-private"}
    hello = client.post(
        "/api/v2/devices/camera-1/hello",
        headers=token,
        json={
            "protocol_version": 2,
            "boot_id": "test-boot-9999",
            "firmware_version": "test",
            "source": "simulator",
            "psram_bytes": 0,
        },
    ).json()
    admin = login(client)
    assert client.post("/api/v2/capture/preview", headers=admin, json={}).status_code == 410
    status = client.get("/api/v2/status").json()
    meta = {
        "session_id": hello["session_id"],
        "boot_id": "test-boot-9999",
        "seq": 1,
        "mode_revision": status["mode_revision"],
        "purpose": "monitoring",
        "capture_age_ms": 0,
    }
    headers = {**token, "Content-Type": "image/jpeg", "X-Frame-Meta": json.dumps(meta)}
    assert (
        client.post(
            "/api/v2/devices/camera-1/frames", headers=headers, content=b"bad-jpeg"
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v2/devices/camera-1/frames", headers=headers, content=b"x" * 524289
        ).status_code
        == 413
    )


def test_dashboard_and_docs_available(client):
    assert "Smart Security" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert "/api/v2/devices/{device_id}/sync" in client.get("/openapi.json").json()["paths"]


def test_login_with_unicode_username_returns_error_not_500(client):
    response = client.post(
        "/api/v2/auth/login", json={"username": "người dùng", "password": "wrong"}
    )
    assert response.status_code == 401


def test_device_requires_bearer_prefix(client):
    response = client.post(
        "/api/v2/devices/camera-1/hello",
        headers={"Authorization": "device-test-token-private"},
        json={},
    )
    assert response.status_code == 401
