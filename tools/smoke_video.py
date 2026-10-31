"""Real loopback WebSocket/HTTP video check with generated JPEGs, never real biometrics."""

from __future__ import annotations

import io
import json
import time
from uuid import uuid4

from PIL import Image
from security_app.contracts import VideoMeta
from security_app.video import VIDEO_MESSAGE_BYTES, pack_video
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect


def run_video(client, address, runtime):
    device = {"Authorization": f"Bearer {(runtime / 'device-token.txt').read_text().strip()}"}
    boot = str(uuid4())
    hello = client.post(
        "devices/camera-1/hello",
        headers=device,
        json={
            "protocol_version": 2,
            "boot_id": boot,
            "firmware_version": "video-smoke",
            "source": "simulator",
            "psram_bytes": 0,
        },
    )
    hello.raise_for_status()
    state = hello.json()
    view = client.post("video/viewers", json={})
    view.raise_for_status()
    lease_id = view.json()["lease_id"]
    url = address.replace("http://", "ws://") + "/api/v2/devices/camera-1/video"
    output = io.BytesIO()
    Image.new("RGB", (640, 480), "#204050").save(output, format="JPEG")
    picture = output.getvalue()
    last_id = None
    try:
        with connect(
            url,
            additional_headers=device,
            compression=None,
            proxy=None,
            max_size=2048,
            max_queue=2,
            close_timeout=1,
        ) as socket:
            assert json.loads(socket.recv(timeout=2))["window_frames"] == 2
            for pair in range(4):
                for offset in [1, 2]:
                    sequence = pair * 2 + offset
                    meta = VideoMeta(
                        protocol_version=1,
                        session_id=state["session_id"],
                        boot_id=boot,
                        seq=sequence,
                        mode_revision=state["mode_revision"],
                        captured_uptime_ms=sequence * 125,
                        sent_uptime_ms=sequence * 125,
                        capture_age_ms=0,
                    )
                    message = pack_video(meta, picture)
                    # A split inside the uint16 prefix verifies actual transport fragmentation.
                    socket.send([message[:1], message[1:101], message[101:]])
                    time.sleep(0.125)
                for offset in [1, 2]:
                    ack = json.loads(socket.recv(timeout=2))
                    assert ack["seq"] == pair * 2 + offset and ack["state"] == "video_received"
                    assert not ack["ai_candidate"]
                    last_id = ack["frame_id"]
            latest = client.get("latest-frame")
            assert latest.headers["X-Frame-Id"] == last_id and latest.content == picture
            with client.stream("GET", f"live.mjpg?lease_id={lease_id}") as response:
                response.raise_for_status()
                chunk = next(response.iter_raw())
                assert b"X-Video-Revision:" in chunk and last_id.encode() in chunk

        # Reject oversized data at the actual server protocol boundary.
        with connect(
            url, additional_headers=device, compression=None, proxy=None, close_timeout=1
        ) as socket:
            socket.recv(timeout=2)
            socket.send(b"x" * (VIDEO_MESSAGE_BYTES + 1))
            try:
                socket.recv(timeout=2)
                raise AssertionError("Oversized WebSocket accepted")
            except ConnectionClosed as exc:
                assert exc.rcvd and exc.rcvd.code == 1009
        try:
            with connect(url, compression=None, proxy=None, open_timeout=2):
                raise AssertionError("Anonymous device socket accepted")
        except InvalidStatus as exc:
            assert exc.response.status_code == 403
        return {
            "source": "generated-JPEG",
            "frames": 8,
            "fragmented_binary": "PASS",
            "two_transport_credits": "PASS",
            "mjpeg": "PASS",
            "oversize_1009": "PASS",
            "anonymous_denied": "PASS",
            "physical_fps_measured": False,
        }
    finally:
        client.delete(f"video/viewers/{lease_id}")
