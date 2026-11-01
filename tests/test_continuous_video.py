"""Synthetic video/AI timing, transport and authorization boundaries; no HIL claims."""

import asyncio
import io
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from security_app.auth import digest
from security_app.contracts import VideoMeta
from security_app.main import create_app
from security_app.runtime import DomainError
from security_app.video import VIDEO_MESSAGE_BYTES, pack_video, unpack_video
from starlette.websockets import WebSocketDisconnect
from test_api import login

pytest_plugins = ["test_worker_boundaries"]


def jpeg():
    data = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(data, format="JPEG")
    return data.getvalue()


def meta(s, seq, **changes):
    data = dict(
        protocol_version=1,
        session_id=s.session_id,
        boot_id=s.boot_id,
        seq=seq,
        mode_revision=s.mode_revision,
        captured_uptime_ms=seq * 125,
        sent_uptime_ms=seq * 125 + 10,
        capture_age_ms=10,
    )
    data.update(changes)
    return VideoMeta(**data)


def wait_for(predicate, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for worker")


def test_video_disarmed_without_ai_or_durable_receipts(service):
    s = service
    view = s.video.renew("operator", None, None)
    assert s.video.demand()["interval_ms"] == 125
    result = s.accept_video(meta(s, 1), jpeg())
    assert result["state"] == "video_received" and not result["ai_candidate"]
    assert s.latest["frame_id"] == result["frame_id"]
    assert not s.pending and not s.processing and not s.latest_analysis
    with s.store.connection() as db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
    s.video.release("operator", view["lease_id"])
    assert not s.video.demand()["enabled"]
    with pytest.raises(DomainError, match="VIDEO_NOT_REQUESTED"):
        s.accept_video(meta(s, 2), jpeg())


def test_video_does_not_wait_for_ai_and_pending_is_newest(blocked):
    s, vision, _ = blocked
    s.set_mode("ARMED", s.mode_revision)
    assert s.video.demand()["interval_ms"] == 250
    first = s.accept_video(meta(s, 1), jpeg())
    assert vision.entered.wait(2)
    second = s.accept_video(meta(s, 2), jpeg())
    third = s.accept_video(meta(s, 3), jpeg())
    assert s.latest["frame_id"] == third["frame_id"]
    assert s.pending.frame_id == third["frame_id"]
    assert s.dropped == 1
    vision.release.set()
    wait_for(lambda: s.latest_analysis and s.latest_analysis["frame_id"] == third["frame_id"])
    assert s.latest_analysis["frame_id"] not in {first["frame_id"], second["frame_id"]}
    assert len(s.analysis_times) == 2
    assert s.analysis_times[1] - s.analysis_times[0] >= 295


def test_old_analysis_revision_keeps_video_but_cannot_analyze(service):
    s = service
    s.video.renew("operator", None, None)
    old = meta(s, 1)
    s.set_mode("ARMED", s.mode_revision)
    result = s.accept_video(old, jpeg())
    assert not result["ai_candidate"] and s.latest["frame_id"] == result["frame_id"]
    assert s.pending is None


def test_enrollment_pauses_on_viewer_release_and_rejects_inflight_result(blocked):
    s, vision, _ = blocked
    s.cfg["profile"] = "hardware"
    person = s.create_person("synthetic enrollment")["id"]
    capture = s.start_capture("enrollment", person)
    view = s.video.renew("operator", None, capture["id"])
    s.accept_video(meta(s, 1, capture_session_id=capture["id"]), jpeg())
    assert vision.entered.wait(2)
    revision = s.mode_revision
    s.video.release("operator", view["lease_id"])
    assert s.mode_revision > revision and not s.video.sampling()
    vision.release.set()
    wait_for(lambda: not s.processing)
    assert s.capture_session["accepted"] == [0, 0]
    with s.store.connection() as db:
        assert not db.execute("SELECT 1 FROM samples WHERE person_id=?", (person,)).fetchone()


def test_enrollment_schedule_is_one_second_and_stops_at_round_completion(blocked):
    s, vision, _ = blocked
    s.cfg["profile"] = "hardware"
    person = s.create_person("scheduled fixture")["id"]
    capture = s.start_capture("enrollment", person)
    s.video.renew("operator", None, capture["id"])
    s.accept_video(meta(s, 1, capture_session_id=capture["id"]), jpeg())
    assert vision.entered.wait(2)
    vision.release.set()
    wait_for(lambda: len(s.analysis_times) == 1 and not s.processing)
    s.accept_video(meta(s, 2, capture_session_id=capture["id"]), jpeg())
    s.accept_video(meta(s, 3, capture_session_id=capture["id"]), jpeg())
    wait_for(lambda: len(s.analysis_times) == 2 and not s.processing)
    assert s.analysis_times[1] - s.analysis_times[0] >= 995
    s.capture_session["accepted"][0] = s.cfg["enrollment"]["per_round_min"]
    result = s.accept_video(meta(s, 4, capture_session_id=capture["id"]), jpeg())
    assert not result["ai_candidate"] and not s.pending
    assert s.latest["frame_id"] == result["frame_id"]


def test_view_expiry_and_owner_isolation(service):
    s = service
    view = s.video.renew("operator", None, None)
    with pytest.raises(DomainError, match="VIDEO_VIEW_EXPIRED"):
        s.video.renew("other", view["lease_id"], None)
    s.video.release("other", view["lease_id"])
    assert s.video.views
    s.video.views[view["lease_id"]]["deadline"] = s.now() - 1
    assert not s.video.demand()["enabled"]


def test_sync_clock_anchor_rejects_delayed_capture_and_resets_at_boot(service):
    from security_app.contracts import Hello

    s = service
    s.video.renew("operator", None, None)
    s.device_clock_offsets.append(s.now() - 3000)
    with pytest.raises(DomainError, match="CAPTURE_TOO_OLD"):
        s.accept_video(meta(s, 1), jpeg())
    s.hello(
        Hello(
            protocol_version=2,
            boot_id="new-clock-boot",
            firmware_version="test",
            source="simulator",
            psram_bytes=0,
        )
    )
    assert not s.device_clock_offsets


@pytest.mark.parametrize(
    "data",
    [b"", b"\0\0x", b"\xff\xffx", b"x" * (VIDEO_MESSAGE_BYTES + 1)],
    ids=["empty", "zero-header", "large-header", "large-message"],
)
def test_binary_envelope_bounds(data):
    with pytest.raises(DomainError):
        unpack_video(data)


def test_ws_auth_binary_sequence_and_no_admin_authority(cfg):
    with TestClient(create_app(cfg)) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v2/devices/camera-1/video"):
                pass
        headers = login(client)
        view = client.post("/api/v2/video/viewers", json={}, headers=headers).json()
        s = client.app.state.service
        from security_app.contracts import Hello

        s.hello(
            Hello(
                protocol_version=2,
                boot_id="video-fixture",
                firmware_version="test",
                source="simulator",
                psram_bytes=0,
            )
        )
        device = {"Authorization": "Bearer device-test-token-private"}
        with client.websocket_connect("/api/v2/devices/camera-1/video", headers=device) as socket:
            assert socket.receive_json() == {"protocol_version": 1, "window_frames": 2}
            socket.send_bytes(pack_video(meta(s, 1), jpeg()))
            response = socket.receive_json()
            assert response["state"] == "video_received"
            assert client.get("/api/v2/latest-frame").content == jpeg()
            socket.send_bytes(pack_video(meta(s, 1), jpeg()))
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()
        with client.websocket_connect("/api/v2/devices/camera-1/video", headers=device) as socket:
            socket.receive_json()
            socket.send_text("not binary")
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()
        assert (
            client.delete(f"/api/v2/video/viewers/{view['lease_id']}", headers=headers).status_code
            == 204
        )


def test_ws_stale_frame_reports_cause_without_private_input_and_releases_slot(cfg, caplog):
    with TestClient(create_app(cfg)) as client:
        headers = login(client)
        client.post("/api/v2/video/viewers", json={}, headers=headers).raise_for_status()
        s = client.app.state.service
        from security_app.contracts import Hello

        s.hello(
            Hello(
                protocol_version=2,
                boot_id="private-boot-not-for-logs",
                firmware_version="test",
                source="simulator",
                psram_bytes=0,
            )
        )
        device = {"Authorization": "Bearer device-test-token-private"}
        s.device_clock_offsets.append(s.now() - 3000)
        with client.websocket_connect("/api/v2/devices/camera-1/video", headers=device) as socket:
            socket.receive_json()
            socket.send_bytes(pack_video(meta(s, 1), jpeg()))
            with pytest.raises(WebSocketDisconnect) as caught:
                socket.receive_json()
            assert caught.value.code == 1008
        records = [r.getMessage() for r in caplog.records if "VIDEO_UPLINK_END" in r.getMessage()]
        assert len(records) == 1
        assert "reason=CAPTURE_TOO_OLD code=1008 accepted_frames=0" in records[0]
        assert "duration_ms=" in records[0] and "idle_ms=" in records[0]
        for private in (s.session_id, s.boot_id, "device-test-token-private", "Authorization"):
            assert private not in records[0]
        # Diagnostics must neither keep the uplink slot nor admit the rejected JPEG.
        assert s.video.latest is None
        s.device_clock_offsets.clear()
        with client.websocket_connect("/api/v2/devices/camera-1/video", headers=device) as socket:
            socket.receive_json()
            socket.send_bytes(pack_video(meta(s, 2), jpeg()))
            assert socket.receive_json()["state"] == "video_received"


@pytest.mark.parametrize("accept", ["*/*", "application/octet-stream"])
def test_mjpeg_event_wakeup_auth_revocation_and_mode_independence(cfg, accept):
    with TestClient(create_app(cfg)) as client:
        headers = login(client)
        lease = client.post("/api/v2/video/viewers", json={}, headers=headers).json()["lease_id"]
        s, auth = client.app.state.service, client.app.state.auth
        owner = digest(client.cookies.get("sss_session"))
        route = next(r.endpoint for r in client.app.routes if r.path == "/api/v2/live.mjpg")
        request = SimpleNamespace(
            cookies=client.cookies, app=client.app, headers={"accept": accept}
        )

        async def exercise():
            response = await route(request, lease, s)
            with s.lock:
                s.video.publish("frame-one", jpeg(), s.now(), s.mode_revision)
            assert b"X-Video-Revision: 1" in await anext(response.body_iterator)
            with s.lock:
                s._disarm("test")
                s.video.publish("frame-two", jpeg(), s.now(), s.mode_revision)
            assert b"X-Frame-Id: frame-two" in await anext(response.body_iterator)
            # Already-open stream must revalidate an expired login, without a 30s reconnect.
            with s.store.connection() as db:
                db.execute("DELETE FROM web_sessions WHERE token_hash=?", (owner,))
            original = s.now
            s.now = lambda: original() + 1001
            with pytest.raises(StopAsyncIteration):
                await anext(response.body_iterator)
            assert auth.session(owner) is None and not s.video.views

        asyncio.run(exercise())
