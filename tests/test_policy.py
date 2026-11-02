import pytest
from security_app.contracts import Observation
from security_app.policy import PolicyEngine

A, B, GAP, STREET = (
    (0.10, 0.25, 0.18, 0.5),
    (0.62, 0.25, 0.18, 0.5),
    (0.43, 0.25, 0.08, 0.5),
    (0.45, 0.01, 0.04, 0.04),
)


def obs(box, kind="UNKNOWN", key="one", valid=True, pid=None):
    return Observation(track_id=key, bbox=box, decision=kind, geometry_valid=valid, person_id=pid)


def run(engine, samples):
    results = []
    for i, observations in enumerate(samples):
        results += engine.ingest(str(i), i * 500, observations)
    return results


def test_unknown_incoming_alarms_once_even_if_stays(cfg):
    events = run(PolicyEngine(cfg), [[obs(A)]] * 2 + [[obs(B)]] * 20)
    assert len([e for e in events if e["audible"]]) == 1
    assert events[0]["reason"] == "unknown_approach"


@pytest.mark.parametrize("boxes", [[A] * 20, [STREET] * 20, [B] * 3 + [A] * 15])
def test_passerby_far_and_outgoing_never_sound(cfg, boxes):
    assert not any(e["audible"] for e in run(PolicyEngine(cfg), [[obs(b)] for b in boxes]))


def test_direct_b_needs_direction_warning_not_siren(cfg):
    events = run(PolicyEngine(cfg), [[obs(B)]] * 16)
    assert len(events) == 1 and events[0]["detail"] == "direction_unresolved"
    assert not events[0]["audible"]


def test_neutral_gap_preserves_recent_a_evidence(cfg):
    events = run(PolicyEngine(cfg), [[obs(A)]] * 2 + [[obs(GAP)]] * 2 + [[obs(B)]] * 6)
    assert any(e["audible"] for e in events)


def test_gap_longer_than_continuity_does_not_inherit_direction(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("a1", 0, [obs(A)])
    engine.ingest("a2", 500, [obs(A)])
    events = []
    for i in range(15):
        events += engine.ingest(f"b{i}", 5000 + i * 500, [obs(B)])
    assert not any(e["audible"] for e in events)


def test_known_does_not_hide_unknown(cfg):
    events = run(
        PolicyEngine(cfg),
        [[obs(A), obs(A, "KNOWN", "two", pid="known")]] * 2
        + [[obs(B), obs(B, "KNOWN", "two", pid="known")]] * 8,
    )
    assert {e["reason"] for e in events} == {"unknown_approach", "known_visit"}


def test_unreadable_face_warns_without_unknown(cfg):
    events = run(PolicyEngine(cfg), [[obs(A, "NO_FACE")]] * 2 + [[obs(B, "NO_FACE")]] * 16)
    assert len(events) == 1 and events[0]["reason"] == "unresolved_presence"
    assert not events[0]["audible"]


def test_face_only_geometry_cannot_sound(cfg):
    events = run(PolicyEngine(cfg), [[obs(A, valid=False)]] * 2 + [[obs(B, valid=False)]] * 16)
    assert not any(e["audible"] for e in events)


def test_duplicate_frame_cannot_add_confirmation(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("a1", 0, [obs(A)])
    engine.ingest("a2", 500, [obs(A)])
    engine.ingest("b1", 1000, [obs(B)])
    votes = list(engine.tracks["one"].votes)
    for i in range(10):
        assert engine.ingest("b1", 1500 + i * 500, [obs(B)]) == []
    assert list(engine.tracks["one"].votes) == votes
    assert engine.tracks["one"].stable_zone == "A"
    assert not engine.tracks["one"].emitted


def test_identity_evidence_uses_distinct_frames_and_elapsed_time(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("a0", 0, [obs(A, "NOT_CHECKED")])
    engine.ingest("a1", 100, [obs(A, "NOT_CHECKED")])
    for now in range(200, 800, 100):
        engine.ingest(f"b{now}", now, [obs(B)])
        assert engine.tracks["one"].confirmed == "UNCERTAIN"
    engine.ingest("b800", 800, [obs(B)])
    assert engine.tracks["one"].confirmed == "UNKNOWN"


def test_250ms_ai_results_are_not_discarded_by_legacy_vote_spacing(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("a0", 0, [obs(A)])
    engine.ingest("a1", 250, [obs(A)])
    for now in [500, 750, 1000, 1250]:
        engine.ingest(f"b{now}", now, [obs(B)])
    assert [v[0] for v in engine.tracks["one"].votes] == [0, 250, 500, 750, 1000, 1250]


def test_new_arrival_when_known_person_stays_has_new_alarm(cfg):
    engine = PolicyEngine(cfg)
    for i in range(8):
        engine.ingest(f"first{i}", i * 500, [obs(A if i < 2 else B, "KNOWN", "host", pid="host")])
    events = []
    for i in range(8):
        events += engine.ingest(
            f"second{i}",
            4000 + i * 500,
            [obs(B, "KNOWN", "host", pid="host"), obs(A if i < 2 else B, key="guest")],
        )
    assert any(e["reason"] == "unknown_approach" and e["track_id"] == "guest" for e in events)


def test_capacity_warning_only_for_observed_zones(cfg):
    engine = PolicyEngine(cfg)
    assert engine.ingest("street", 0, [obs(STREET, key=str(i)) for i in range(4)]) == []
    events = engine.ingest("door", 500, [obs(B, key=str(i)) for i in range(3)])
    assert len(events) == 1 and events[0]["reason"] == "capacity_exceeded"


def test_global_cooldown_does_not_hide_second_event_or_delay_siren(cfg):
    engine = PolicyEngine(cfg)
    events = run(engine, [[obs(A)]] * 2 + [[obs(B)]] * 6)
    for i in range(8):
        events += engine.ingest(f"new{i}", 4000 + i * 500, [obs(A if i < 2 else B, key="second")])
    alarms = [e for e in events if e["reason"] == "unknown_approach"]
    assert len(alarms) == 2 and alarms[1]["suppression"] == "cooldown"
    later = engine.ingest("later", 16000, [obs(B, key="second")])
    assert all(e["reason"] == "visit_completed" and not e["audible"] for e in later)
