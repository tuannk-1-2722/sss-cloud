"""Approved monitoring quality and UNKNOWN boundaries; synthetic, not AI accuracy."""

from types import SimpleNamespace

import numpy as np
import pytest
from security_app.config import calibration_status, camera_profile_hash, validate_config
from security_app.policy import PolicyEngine
from security_app.vision import OpenCVVision
from test_policy import A, B, obs


@pytest.mark.parametrize("span,expected", [(0, "UNCERTAIN"), (399, "UNCERTAIN"), (400, "UNKNOWN")])
def test_unknown_requires_two_distinct_images_spanning_400ms(cfg, span, expected):
    engine = PolicyEngine(cfg)
    engine.ingest("first", 0, [obs(A)])
    engine.ingest("second", span, [obs(A)])
    assert engine.tracks["one"].confirmed == expected


def test_duplicate_frame_and_ineligible_images_do_not_supply_second_unknown_vote(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("first", 0, [obs(A)])
    engine.ingest("first", 400, [obs(A)])
    engine.ingest("blur", 500, [obs(A, "UNCERTAIN").model_copy(update={"reason": "FACE_BLURRED"})])
    engine.ingest("no-face", 600, [obs(A, "NO_FACE")])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    assert engine.snapshot()[0]["unknown_votes"] == 1
    engine.ingest("valid-second", 700, [obs(A)])
    assert engine.tracks["one"].confirmed == "UNKNOWN"


def test_known_evidence_still_blocks_unknown_and_keeps_original_known_rule(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("known", 0, [obs(A, "KNOWN", pid="person")])
    engine.ingest("u1", 400, [obs(A)])
    engine.ingest("u2", 800, [obs(A)])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    engine = PolicyEngine(cfg)
    for i, now in enumerate((0, 400)):
        engine.ingest(str(i), now, [obs(A, "KNOWN", pid="person")])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    engine.ingest("third", 600, [obs(A, "KNOWN", pid="person")])
    assert engine.tracks["one"].confirmed == "KNOWN"


def test_known_after_unknown_confirmation_cancels_unknown_before_any_new_alarm(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("a0", 0, [obs(A)])
    engine.ingest("a1", 400, [obs(A)])
    for now, kind in ((700, "KNOWN"), (1100, "NO_FACE"), (1500, "NO_FACE")):
        events = engine.ingest(
            str(now), now, [obs(B, kind, pid="person" if kind == "KNOWN" else None)]
        )
        assert not any(e["audible"] for e in events)
    assert engine.tracks["one"].confirmed == "UNCERTAIN"


@pytest.mark.parametrize("valid", [True, False])
def test_confirmed_unknown_triggers_before_b_dwell_for_body_or_face_only(cfg, valid):
    engine = PolicyEngine(cfg)
    engine.ingest("a0", 0, [obs(A, valid=valid)])
    confirmed = engine.ingest("a1", 400, [obs(A, valid=valid)])
    assert engine.tracks["one"].confirmed == "UNKNOWN"
    assert any(e["audible"] for e in confirmed)
    for now in (700, 1100, 1499):
        assert not any(e["audible"] for e in engine.ingest(str(now), now, [obs(B, valid=valid)]))
    events = engine.ingest("dwell", 1500, [obs(B, valid=valid)])
    assert not any(e["audible"] for e in events)


def test_unknown_expires_and_never_reuses_votes_from_previous_visit(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("b0", 0, [obs(B)])
    engine.ingest("b1", 400, [obs(B)])
    engine.ingest("expired", 2401, [obs(B, "NO_FACE")])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    assert engine.snapshot()[0]["identity_reason"] == "IDENTITY_EVIDENCE_EXPIRED"
    engine.ingest("a0", 2500, [obs(A, "NO_FACE")])
    engine.ingest("a1", 2800, [obs(A, "NO_FACE")])
    engine.ingest("new-b0", 3100, [obs(B)])
    engine.ingest("new-b1", 3400, [obs(B, "NO_FACE")])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    assert engine.snapshot()[0]["unknown_votes"] == 1


def test_same_moderately_blurred_image_is_usable_for_monitoring_but_not_enrollment(cfg):
    vision = OpenCVVision.__new__(OpenCVVision)
    vision.cfg = cfg
    vision.recognizer = SimpleNamespace(alignCrop=lambda image, _: image.copy())
    pixels = np.tile(np.array([120, 123, 120, 117], dtype=np.uint8), (112, 28))
    image = np.repeat(pixels[:, :, None], 3, axis=2)
    face = np.array([0, 0, 112, 112, 35, 40, 75, 40, 56, 60, 40, 85, 72, 85, 0.99])
    aligned, measured = vision.quality(image, face)
    assert cfg["vision"]["monitoring_blur_min"] <= measured["blur"] < cfg["vision"]["blur_min"]
    assert aligned is not None
    aligned, measured = vision.quality(image, face, enrollment=True)
    assert aligned is None and measured["reason"] == "FACE_BLURRED"
    assert vision.quality(np.full_like(image, 120), face)[1]["reason"] == "FACE_BLURRED"


def test_detector_threshold_is_reset_for_each_workflow_on_the_same_worker(cfg):
    class Detector:
        threshold = None

        def setScoreThreshold(self, value):
            self.threshold = value

        def setInputSize(self, value):
            pass

        def detect(self, image):
            return None, np.zeros((1, 15)) if self.threshold <= 0.85 else None

    vision = OpenCVVision.__new__(OpenCVVision)
    vision.cfg, vision.face_detector = cfg, Detector()
    image = np.zeros((480, 640, 3), np.uint8)
    assert len(vision.faces(image)) == 1
    assert vision.faces(image, enrollment=True) == []
    assert len(vision.faces(image)) == 1


@pytest.mark.parametrize("failure", ["size", "landmarks", "exposure"])
def test_relaxed_monitoring_still_rejects_invalid_face_evidence(cfg, failure):
    vision = OpenCVVision.__new__(OpenCVVision)
    vision.cfg = cfg
    vision.recognizer = SimpleNamespace(alignCrop=lambda image, _: image.copy())
    pixels = np.tile(np.array([120, 123, 120, 117], dtype=np.uint8), (112, 28))
    image = np.repeat(pixels[:, :, None], 3, axis=2)
    face = np.array([0, 0, 112, 112, 35, 40, 75, 40, 56, 60, 40, 85, 72, 85, 0.99])
    if failure == "size":
        face[2] = 79
        reason = "FACE_TOO_SMALL"
    elif failure == "landmarks":
        face[4] = np.nan
        reason = "LANDMARKS_INVALID"
    else:
        pixels = np.tile(np.array([245, 255, 245, 235], dtype=np.uint8), (112, 28))
        image = np.repeat(pixels[:, :, None], 3, axis=2)
        reason = "FACE_EXPOSURE"
    aligned, quality = vision.quality(image, face)
    assert aligned is None and quality["reason"] == reason


def test_completion_preserves_whole_visit_quality_counts_without_echoing_private_reasons(cfg):
    engine = PolicyEngine(cfg)
    for i in range(2):
        engine.ingest(
            str(i), i * 400, [obs(B, "UNCERTAIN").model_copy(update={"reason": "FACE_BLURRED"})]
        )
    engine.ingest(
        "private", 800, [obs(B, "UNCERTAIN").model_copy(update={"reason": "secret-token"})]
    )
    engine.ingest("last", 1200, [obs(B, "NO_FACE")])
    final = engine.advance(3800)[0]
    assert final["identity"] == "UNCERTAIN" and not final["audible"]
    assert final["identity_evidence"]["reason_counts"]["FACE_BLURRED"] == 2
    assert final["identity_evidence"]["observation_counts"]["NO_FACE"] == 1
    assert "secret-token" not in str(final)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("vision", "monitoring_face_confidence", 0),
        ("vision", "monitoring_face_confidence", 0.95),
        ("vision", "monitoring_blur_min", 0),
        ("vision", "monitoring_blur_min", 61),
        ("policy", "unknown_confirm_votes", 1),
        ("policy", "unknown_confirmation_span_ms", 399),
        ("policy", "unknown_confirmation_span_ms", 4001),
    ],
)
def test_monitoring_and_unknown_bounds_fail_closed(cfg, section, key, value):
    cfg[section][key] = value
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_new_settings_invalidate_previous_calibration_without_rewriting_it(
    cfg, tmp_path, monkeypatch
):
    import json
    from copy import deepcopy

    old = deepcopy(cfg)
    for key in ("monitoring_blur_min", "monitoring_face_confidence"):
        old["vision"].pop(key, None)
    for key in ("unknown_confirm_votes", "unknown_confirmation_span_ms"):
        old["policy"].pop(key, None)
    path = tmp_path / "old-calibration.json"
    path.write_text(
        json.dumps({"state": "validated", "camera_profile_hash": camera_profile_hash(old)})
    )
    original = path.read_bytes()
    monkeypatch.setenv("SSS_CALIBRATION", str(path))
    cfg["profile"] = "hardware"
    assert calibration_status(cfg)[1] == "CAMERA_PROFILE_CHANGED"
    assert path.read_bytes() == original
