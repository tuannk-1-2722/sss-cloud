"""Run the actual worker across an inference barrier; these are not pre-queue races."""

import io
import threading
import time

import numpy as np
import pytest
from conftest import seed
from PIL import Image
from security_app.contracts import FrameMeta, Hello
from security_app.service import SecurityService


class BlockingVision:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.initialized = threading.Event()
        self.resets = 0

    def detections(self, _):
        self.initialized.set()
        return [], []

    def reset(self):
        self.resets += 1

    def analyze(self, *_):
        self.entered.set()
        assert self.release.wait(3), "test failed to release inference"
        return []

    def enrollment_sample(self, _):
        self.analyze()
        return np.eye(1, 128, 1, dtype=np.float32).reshape(-1), {
            "reason": None,
            "perceptual_hash": "0123456789abcdef",
        }


def wait_receipt(s, frame_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with s.store.connection() as db:
            row = db.execute("SELECT state FROM receipts WHERE frame_id=?", (frame_id,)).fetchone()
        if row and row[0] not in ("accepted", "processing"):
            return row[0]
        time.sleep(0.01)
    raise AssertionError("worker stalled")


@pytest.fixture
def blocked(cfg):
    vision = BlockingVision()
    s = SecurityService(cfg, vision_factory=lambda _: vision)
    assert vision.initialized.wait(2)
    pid = seed(s)
    s.hello(
        Hello(
            protocol_version=2,
            boot_id="blocked-test",
            firmware_version="test",
            source="simulator",
            psram_bytes=0,
        )
    )
    try:
        yield s, vision, pid
    finally:
        vision.release.set()
        s.close()


@pytest.mark.parametrize("mutation", ["disarm", "gallery"])
def test_result_after_inference_is_cancelled(blocked, mutation):
    s, v, pid = blocked
    s.set_mode("ARMED", s.mode_revision)
    meta = FrameMeta(
        session_id=s.session_id,
        boot_id=s.boot_id,
        seq=1,
        mode_revision=s.mode_revision,
        purpose="monitoring",
        capture_age_ms=0,
    )
    frame = s.accept(meta, observations=[])
    assert v.entered.wait(2)
    if mutation == "disarm":
        s.set_mode("DISARMED", s.mode_revision)
    else:
        s.deactivate_person(pid)
    v.release.set()
    assert wait_receipt(s, frame["frame_id"]) == "cancelled"
    assert not s.store.event_rows()


def test_enrollment_expires_while_inference_runs_without_status_poll(blocked):
    s, v, _ = blocked
    s.cfg["profile"] = "hardware"
    person = s.create_person("enrollment fixture")["id"]
    capture = s.start_capture("enrollment", person)
    s.video.renew("fixture-viewer", None, capture["id"])
    meta = FrameMeta(
        session_id=s.session_id,
        boot_id=s.boot_id,
        seq=1,
        mode_revision=s.mode_revision,
        purpose="enrollment",
        capture_session_id=capture["id"],
        capture_age_ms=0,
    )
    image = io.BytesIO()
    Image.new("RGB", (640, 480)).save(image, format="JPEG")
    frame = s.accept(meta, jpeg=image.getvalue())
    assert v.entered.wait(2)
    with s.lock:
        s.capture_session["deadline"] = s.now() - 1
    v.release.set()
    assert wait_receipt(s, frame["frame_id"]) == "cancelled"
    assert s.capture_session is None and s.mode == "DISARMED"
    with s.store.connection() as db:
        assert not db.execute("SELECT 1 FROM samples WHERE person_id=?", (person,)).fetchone()


def test_round_change_does_not_relabel_inflight_sample(blocked):
    s, v, _ = blocked
    s.cfg["profile"] = "hardware"
    person = s.create_person("round fixture")["id"]
    capture = s.start_capture("enrollment", person)
    s.video.renew("fixture-viewer", None, capture["id"])
    meta = FrameMeta(
        session_id=s.session_id,
        boot_id=s.boot_id,
        seq=1,
        mode_revision=s.mode_revision,
        purpose="enrollment",
        capture_session_id=capture["id"],
        capture_age_ms=0,
    )
    image = io.BytesIO()
    Image.new("RGB", (640, 480)).save(image, format="JPEG")
    frame = s.accept(meta, jpeg=image.getvalue())
    assert v.entered.wait(2)
    with s.lock:
        s.capture_session["accepted"][0] = 5
    s.next_enrollment_round(capture["id"])
    v.release.set()
    assert wait_receipt(s, frame["frame_id"]) == "cancelled"
    assert s.capture_session["accepted"][1] == 0
