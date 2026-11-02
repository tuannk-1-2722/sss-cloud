"""Short-visit policy boundaries with synthetic observations, never AI accuracy."""

from security_app.policy import PolicyEngine
from test_policy import A, B, obs


def test_known_approach_evidence_survives_entering_b_when_face_turns_away(cfg):
    engine = PolicyEngine(cfg)
    for now in (0, 300, 600):
        assert engine.ingest(str(now), now, [obs(A, "KNOWN", pid="person")]) == []
    assert engine.tracks["one"].confirmed == "KNOWN"
    engine.ingest("b0", 900, [obs(B, "NO_FACE")])
    events = engine.ingest("b1", 1200, [obs(B, "NO_FACE")])
    assert [(e["reason"], e["person_id"]) for e in events] == [("known_visit", "person")]
    assert not any(e["audible"] for e in events)


def test_identity_survives_neutral_gap_inside_acquisition_region(cfg):
    from test_policy import GAP

    engine = PolicyEngine(cfg)
    for i, zone in enumerate((A, A, GAP, GAP)):
        observation = obs(zone, "KNOWN", pid="person").model_copy(
            update={"identity_eligible": True}
        )
        engine.ingest(str(i), i * 300, [observation])
    engine.ingest("b0", 1200, [obs(B, "NO_FACE")])
    events = engine.ingest("b1", 1500, [obs(B, "NO_FACE")])
    assert any(e["reason"] == "known_visit" and e["person_id"] == "person" for e in events)


def test_short_unreadable_visit_has_explicit_final_result_without_unknown_alarm(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("b0", 0, [obs(B, "NO_FACE")])
    engine.ingest("b1", 300, [obs(B, "NO_FACE")])
    events = engine.ingest("empty", 3000, [])
    completed = [e for e in events if e["reason"] == "visit_completed"]
    assert len(completed) == 1
    assert completed[0]["identity"] == "UNCERTAIN"
    assert completed[0]["completion_reason"] == "TRACK_GAP"
    assert not any(e["audible"] for e in events)
    assert engine.ingest("empty-again", 3500, []) == []


def test_reentering_b_does_not_reuse_previous_visits_known_identity(cfg):
    engine = PolicyEngine(cfg)
    for i in range(5):
        engine.ingest(str(i), i * 300, [obs(B, "KNOWN", pid="person")])
    old_visit = engine.tracks["one"].visit_id
    engine.ingest("a0", 1500, [obs(A, "NO_FACE")])
    events = engine.ingest("a1", 1800, [obs(A, "NO_FACE")])
    assert any(e["reason"] == "visit_completed" for e in events)
    engine.ingest("new-b0", 2100, [obs(B, "NO_FACE")])
    events = engine.ingest("new-b1", 2400, [obs(B, "NO_FACE")])
    assert engine.tracks["one"].visit_id != old_visit
    assert not any(e["reason"] == "known_visit" for e in events)


def test_expired_approach_identity_cannot_mark_doorstep_known(cfg):
    engine = PolicyEngine(cfg)
    for now in (0, 300, 600):
        engine.ingest(str(now), now, [obs(A, "KNOWN", pid="person")])
    engine.ingest("b0", 2700, [obs(B, "NO_FACE")])
    events = engine.ingest("b1", 3000, [obs(B, "NO_FACE")])
    assert not any(e["reason"] == "known_visit" for e in events)


def test_unknown_alarms_during_approach_and_does_not_wait_for_b_dwell(cfg):
    engine = PolicyEngine(cfg)
    early = []
    for now in (0, 300, 600):
        early.extend(engine.ingest(str(now), now, [obs(A)]))
    assert len([e for e in early if e["audible"]]) == 1
    for now in (900, 1200, 1500):
        assert not any(e["audible"] for e in engine.ingest(str(now), now, [obs(B)]))
    events = engine.ingest("b-ready", 1800, [obs(B)])
    assert not any(e["audible"] for e in events)


def test_missing_observation_restarts_pending_b_dwell(cfg):
    engine = PolicyEngine(cfg)
    for now in (0, 300, 600):
        engine.ingest(str(now), now, [obs(A)])
    engine.ingest("b-interrupted", 900, [obs(B)])
    engine.ingest("missing", 1000, [])
    events = []
    for now in (1300, 1600, 1900):
        events.extend(engine.ingest(str(now), now, [obs(B)]))
    assert engine.tracks["one"].entered_b == 1300
    assert not any(e["audible"] for e in events)


def test_idle_visit_completion_does_not_create_delayed_alarm(cfg):
    engine = PolicyEngine(cfg)
    for i, zone in enumerate((A, A, B, B)):
        engine.ingest(str(i), i * 300, [obs(zone)])
    events = engine.advance(3600)
    assert len(events) == 1 and events[0]["reason"] == "visit_completed"
    assert not events[0]["audible"]


def test_reset_discards_pending_visit_completion(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("b0", 0, [obs(B, "NO_FACE")])
    engine.ingest("b1", 300, [obs(B, "NO_FACE")])
    engine.reset()
    assert engine.advance(3000) == []
    assert engine.recent_visits == []


def test_conflicting_identity_cannot_finalize_as_previous_known_person(cfg):
    engine = PolicyEngine(cfg)
    for now in (0, 300, 600):
        engine.ingest(str(now), now, [obs(B, "KNOWN", pid="first")])
    engine.ingest("conflict", 900, [obs(B, "KNOWN", pid="second")])
    final = engine.advance(4000)
    assert final[0]["identity"] == "UNCERTAIN"
    assert final[0]["person_id"] is None
    assert final[0]["identity_reason"] == "IDENTITY_CONFLICT"


def test_worker_finishes_short_visit_without_waiting_for_another_camera_frame(service):
    import time

    service.cfg["policy"]["track_gap_ms"] = 100
    service.set_mode("ARMED", service.mode_revision)
    with service.lock:
        now = service.now()
        service.policy.ingest("b0", now - 50, [obs(B, "NO_FACE")])
        service.policy.ingest("b1", now, [obs(B, "NO_FACE")])
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(e["reason"] == "visit_completed" for e in service.store.event_rows()):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("Visit did not finish while camera was quiet")
    result = service.status()["recent_visits"][-1]
    assert result["identity"] == "UNCERTAIN" and not result["audible"]
    assert not service.command or service.command["type"] != "START_ALARM"


def test_disarm_prevents_idle_visit_commit(service):
    service.set_mode("ARMED", service.mode_revision)
    with service.lock:
        now = service.now()
        service.policy.ingest("b0", now - 300, [obs(B, "NO_FACE")])
        service.policy.ingest("b1", now, [obs(B, "NO_FACE")])
    service.set_mode("DISARMED", service.mode_revision)
    with service.lock:
        service._finish_visits()
    assert service.store.event_rows() == []


def test_new_flow_cannot_silently_reuse_old_hardware_calibration(cfg, tmp_path, monkeypatch):
    import json
    from copy import deepcopy

    from security_app.config import calibration_status, camera_profile_hash

    old = deepcopy(cfg)
    old["analysis"].pop("housekeeping_ms")
    old["policy"].pop("flow_version")
    old["policy"].pop("completed_history_limit")
    old["vision"].pop("recognition_region")
    profile = tmp_path / "previous-calibration.json"
    profile.write_text(
        json.dumps({"state": "validated", "camera_profile_hash": camera_profile_hash(old)})
    )
    original = profile.read_bytes()
    monkeypatch.setenv("SSS_CALIBRATION", str(profile))
    cfg["profile"] = "hardware"
    assert calibration_status(cfg)[1] == "CAMERA_PROFILE_CHANGED"
    assert profile.read_bytes() == original
