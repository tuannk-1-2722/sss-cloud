"""Regression cases found during the v2 review; synthetic inputs, no hardware claims."""

import sqlite3

import numpy as np
import pytest
from pydantic import ValidationError
from security_app.config import load_config, validate_config
from security_app.contracts import FrameMeta, Observation, Sync, SyntheticFrame
from security_app.policy import PolicyEngine
from security_app.service import DomainError
from security_app.vision import OpenCVVision


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("zones", "stable_observations", 0),
        ("camera", "active_ms", 0),
        ("vision", "margin", -0.1),
        ("vision", "face_confidence", 2),
        ("policy", "confirm_votes", True),
        ("policy", "vote_window", 2.5),
        ("alarm", "test_cap_ms", 2000),
        ("alarm", "control_lost_ms", 8000),
        ("vision", "blur_min", float("nan")),
        ("notifications", "max_attempts", 0),
    ],
)
def test_invalid_operational_config(cfg, section, key, value):
    cfg[section][key] = value
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_explicit_missing_config_is_not_silent_simulation(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing-hardware.yaml")


def test_duplicate_track_in_same_frame_is_invalid():
    observation = dict(track_id="same", bbox=(0.6, 0.2, 0.2, 0.5), decision="UNKNOWN")
    meta = dict(
        session_id="s",
        boot_id="boot-0001",
        seq=1,
        mode_revision=1,
        purpose="monitoring",
        capture_age_ms=0,
    )
    with pytest.raises(ValidationError):
        SyntheticFrame(metadata=meta, observations=[observation, observation])


def test_bool_is_not_frame_sequence():
    with pytest.raises(ValidationError):
        FrameMeta(
            session_id="s",
            boot_id="boot-0001",
            seq=True,
            mode_revision=1,
            purpose="monitoring",
            capture_age_ms=0,
        )


def test_sync_same_sequence_changed_payload_conflicts(service):
    req = Sync(session_id=service.session_id, boot_id=service.boot_id, sync_seq=1, uptime_ms=1)
    service.sync(req)
    with pytest.raises(DomainError, match="SYNC_ID_REUSED"):
        service.sync(req.model_copy(update={"uptime_ms": 2}))


def test_arm_checks_calibrated_capacity(service):
    service.cfg["profile"] = "hardware"
    service.calibration = {"validated_gallery_limit": 0}
    service.calibration_error = None
    with pytest.raises(DomainError, match="CALIBRATED_CAPACITY_REACHED"):
        service.set_mode("ARMED", service.mode_revision)


def test_disarm_db_failure_clears_memory_before_writing(service, monkeypatch):
    service.action("review-test-0001", "test")
    monkeypatch.setattr(
        service,
        "_cancel_pending",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")),
    )
    with pytest.raises(DomainError, match="STORAGE_FAULT"):
        service.set_mode("DISARMED", service.mode_revision)
    assert service.mode == "DISARMED"
    assert service.command is None or service.command["type"] == "STOP_ALARM"


def test_delete_any_person_cancels_other_capture(service):
    victim = service.test_person_id
    other = service.create_person("other")["id"]
    with service.store.connection() as db:
        db.execute("UPDATE people SET active=1 WHERE id=?", (other,))
    another = service.create_person("capture subject")["id"]
    service.cfg["profile"] = "hardware"
    service.start_capture("enrollment", another)
    old_revision = service.mode_revision
    service.deactivate_person(victim, delete=True)
    assert service.capture_session is None
    assert service.mode_revision > old_revision


def test_geometry_change_cannot_reuse_incoming_votes(cfg):
    e = PolicyEngine(cfg)

    def observation(box, valid=True):
        return Observation(track_id="one", bbox=box, decision="UNKNOWN", geometry_valid=valid)

    a, b = (0.1, 0.2, 0.2, 0.5), (0.6, 0.2, 0.2, 0.5)
    for i, box in enumerate([a, a, b, b, b]):
        e.ingest(str(i), i * 500, [observation(box)])
    events = e.ingest("ambiguous", 2500, [observation(b, False)])
    events += e.ingest("resolved", 3000, [observation(b)])
    assert not any(d["audible"] for d in events)


def test_absent_frame_breaks_consecutive_zone_confirmation(cfg):
    e = PolicyEngine(cfg)
    a = Observation(track_id="one", bbox=(0.1, 0.2, 0.2, 0.5), decision="NOT_CHECKED")
    e.ingest("1", 0, [a])
    e.ingest("2", 500, [])
    e.ingest("3", 1000, [a])
    assert e.tracks["one"].stable_zone != "A"


def fake_vision(cfg, persons, faces):
    v = OpenCVVision.__new__(OpenCVVision)
    v.cfg = cfg
    v.decode = lambda _: np.zeros((480, 640, 3), np.uint8)
    v.detections = lambda _: (persons, faces)
    v.faces = lambda _, **_kwargs: faces
    v.quality = lambda *_args, **_kwargs: (np.zeros((112, 112, 3), np.uint8), {"reason": None})
    v.feature = lambda _: np.eye(1, 128, dtype=np.float32).reshape(-1)
    return v


def test_enrollment_face_outside_guide_rejected(cfg):
    face = np.array([50, 100, 100, 100] + [60, 110] * 5 + [0.99], np.float32)
    v = fake_vision(cfg, [(0.05, 0.1, 0.3, 0.8)], [face])
    vector, quality = v.enrollment_sample(b"fixture")
    assert vector is None and quality["reason"] == "FACE_OUTSIDE_GUIDE"


def test_enrollment_centered_face_outside_monitoring_b_does_not_run_person_model(cfg):
    face = np.array([200, 180, 100, 100] + [210, 190] * 5 + [0.99], np.float32)
    v = fake_vision(cfg, [], [face])
    v.decode = lambda _: np.full((480, 640, 3), 128, np.uint8)

    def no_person_detector(_):
        raise AssertionError("Enrollment must not run NanoDet")

    v.detections = no_person_detector
    vector, quality = v.enrollment_sample(b"fixture")
    assert vector is not None and quality["reason"] is None
    v.faces = lambda _, **_kwargs: [face, face]
    vector, quality = v.enrollment_sample(b"fixture")
    assert vector is None and quality["reason"] == "EXACTLY_ONE_FACE_REQUIRED"


def test_ambiguous_face_does_not_become_extra_person(cfg):
    from security_app.vision import ConservativeTracker

    face = np.array([430, 100, 100, 100] + [440, 110] * 5 + [0.99], np.float32)
    v = fake_vision(cfg, [(0.55, 0.1, 0.35, 0.8), (0.6, 0.1, 0.35, 0.8)], [face])
    v.tracker = ConservativeTracker(cfg)
    results = v.analyze(b"fixture", {}, 0)
    assert len(results) == 2
    assert all(not o.geometry_valid and o.decision == "UNCERTAIN" for o in results)


def test_known_id_change_clears_confirmation_after_votes_age_out(cfg):
    from security_app.policy import Track

    cfg["policy"]["identity_fresh_ms"] = 10000
    engine = PolicyEngine(cfg)
    track = Track(last_seen=0, confirmed="KNOWN", person_id="old", last_eligible=1000)
    track.votes.append((1000, "KNOWN", "old"))
    observation = Observation(
        track_id="one", bbox=(0.6, 0.2, 0.2, 0.5), decision="KNOWN", person_id="new"
    )
    engine._vote(track, observation, 6000)
    assert track.confirmed == "UNCERTAIN" and track.person_id is None
