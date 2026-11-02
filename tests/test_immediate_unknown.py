"""Final demo rule: confirmed UNKNOWN immediately requests an alarm, without geometry gates."""

import pytest
from security_app.policy import PolicyEngine
from test_policy import GAP, A, B, obs


@pytest.mark.parametrize("box,geometry", [(A, True), (B, True), (B, False), (GAP, False)])
def test_confirmed_unknown_alarms_immediately_without_zone_direction_dwell(cfg, box, geometry):
    engine = PolicyEngine(cfg)
    first = obs(box, valid=geometry).model_copy(update={"identity_eligible": True})
    assert not engine.ingest("first", 0, [first])
    events = engine.ingest("second", 400, [first])
    alarm = [e for e in events if e["reason"] == "unknown_approach"]
    assert len(alarm) == 1 and alarm[0]["audible"]
    assert engine.tracks["one"].confirmed == "UNKNOWN"


def test_unknown_alarm_is_not_reissued_when_same_track_moves_from_a_to_b(cfg):
    engine = PolicyEngine(cfg)
    events = []
    for i, box in enumerate((A, A, B, B, B, B)):
        events.extend(engine.ingest(str(i), i * 400, [obs(box)]))
    assert len([e for e in events if e["reason"] == "unknown_approach"]) == 1


def test_new_confirmed_stranger_is_not_suppressed_by_previous_strangers_cooldown(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("first0", 0, [obs(B)])
    events = engine.ingest("first1", 400, [obs(B)])
    assert any(e["audible"] for e in events)
    engine.ingest("second0", 500, [obs(B, key="second")])
    events = engine.ingest("second1", 900, [obs(B, key="second")])
    assert any(e["audible"] for e in events)


def test_single_unknown_or_duplicate_frame_never_concludes_unknown(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [obs(B)])
    assert not engine.ingest("one", 400, [obs(B)])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
