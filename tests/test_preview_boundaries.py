"""Preview transport checks with generated JPEGs; no hardware/AI accuracy claims."""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from security_app.contracts import VideoMeta
from security_app.main import create_app
from security_app.video import pack_video
from starlette.websockets import WebSocketDisconnect


class PreviewVision:
    def __init__(self, _config):
        pass

    def detections(self, _image):
        return [], []

    def reset(self):
        pass

    def analyze(self, *_args):
        raise AssertionError("Preview must not require person/face inference")

    def enrollment_sample(self, *_args):
        raise AssertionError("Preview must not require enrollment quality checks")


@pytest.fixture
def preview_client(cfg, monkeypatch):
    monkeypatch.setenv("SSS_HTTPS", "0")
    monkeypatch.setattr("security_app.vision.OpenCVVision", PreviewVision)
    cfg["profile"] = "hardware"
    with TestClient(create_app(cfg)) as client:
        device = {"Authorization": "Bearer device-test-token-private"}
        hello = client.post(
            "/api/v2/devices/camera-1/hello",
            headers=device,
            json={
                "protocol_version": 2,
                "boot_id": "preview-boundary-test",
                "firmware_version": "test",
                "source": "hardware",
                "psram_bytes": 4194304,
            },
        )
        assert hello.status_code == 200
        login = client.post(
            "/api/v2/auth/login",
            json={"username": "admin", "password": "test-password-private"},
        )
        assert login.status_code == 200
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        view = client.post("/api/v2/video/viewers", headers=headers, json={}).json()
        metadata = VideoMeta(
            protocol_version=1,
            session_id=hello.json()["session_id"],
            boot_id="preview-boundary-test",
            seq=1,
            mode_revision=client.get("/api/v2/status").json()["mode_revision"],
            captured_uptime_ms=100,
            sent_uptime_ms=110,
            capture_age_ms=10,
        )
        with client.websocket_connect("/api/v2/devices/camera-1/video", headers=device) as socket:
            socket.receive_json()
            yield client, socket, metadata, view["lease_id"]


def jpeg_for(scene):
    if scene == "texture":
        pixels = np.random.default_rng(0).integers(0, 256, (480, 640, 3), dtype=np.uint8)
        image = Image.fromarray(pixels)
    else:
        image = Image.new("RGB", (640, 480), "black" if scene == "dark" else "white")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def upload_preview(client, socket, metadata, jpeg):
    socket.send_bytes(pack_video(metadata, jpeg))
    response = socket.receive_json()
    latest = client.get("/api/v2/latest-frame")
    assert latest.status_code == 200
    assert latest.headers["X-Frame-Id"] == response["frame_id"]
    assert latest.content == jpeg
    return latest


@pytest.mark.parametrize("scene", ["dark", "bright", "texture"])
def test_preview_accepts_scene_without_person_or_quality_gate(preview_client, scene):
    client, socket, metadata, _ = preview_client
    jpeg = jpeg_for(scene)
    if scene == "texture":
        # Exercise a payload above both the suspected DMA boundary and VGA buffer capacity.
        # Pillow quality is not the OV2640 quality scale; this only checks backend transport.
        assert 61440 < len(jpeg) < 524288
    upload_preview(client, socket, metadata, jpeg)
    status = client.get("/api/v2/status").json()
    assert not status["capture"]["active"] and status["mode"] == "DISARMED"
    assert status["worker_error"] is None
    assert client.app.state.service.latest["mode_revision"] == status["mode_revision"]


def test_video_view_expiry_stops_upload_and_stale_cache_is_not_returned(preview_client):
    client, socket, metadata, lease = preview_client
    assert client.get("/api/v2/latest-frame").status_code == 404
    upload_preview(client, socket, metadata, jpeg_for("bright"))
    service = client.app.state.service
    with service.lock:
        service.video.views[lease]["deadline"] = service.now() - 1
        service.latest["received_ms"] = service.now() - 4000
    assert client.get("/api/v2/latest-frame").status_code == 404
    metadata.seq += 1
    metadata.captured_uptime_ms += 125
    metadata.sent_uptime_ms += 125
    socket.send_bytes(pack_video(metadata, jpeg_for("dark")))
    with pytest.raises(WebSocketDisconnect):
        socket.receive_json()
