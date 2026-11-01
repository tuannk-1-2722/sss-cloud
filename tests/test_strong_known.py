"""Boundaries of known-only confirmation; synthetic evidence is not AI accuracy."""

import pytest
from security_app.config import camera_profile_hash, validate_config
from security_app.contracts import FaceEvidence
from security_app.policy import PolicyEngine
from test_policy import A, B, obs


def strong(box=B, *, pid="person", score=0.7, second=None):
    return obs(box, "KNOWN", pid=pid).model_copy(
        update={"evidence": FaceEvidence(s1=score, s2=second)}
    )


def test_two_strong_images_can_confirm_from_approach_without_restarting_at_lock(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("a0", 0, [strong(A)])
    engine.ingest("a1", 500, [strong(A)])
    assert engine.tracks["one"].confirmed == "KNOWN"
    engine.ingest("b0", 800, [obs(B, "NO_FACE")])
    events = engine.ingest("b1", 1100, [obs(B, "NO_FACE")])
    assert any(e["reason"] == "known_visit" for e in events)
    assert not any(e["audible"] for e in events)


@pytest.mark.parametrize("span", [0, 499])
def test_same_time_or_too_close_images_cannot_confirm(cfg, span):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [strong()])
    engine.ingest("two", span, [strong()])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"


@pytest.mark.parametrize("case", ["score", "margin", "missing", "disabled"])
def test_weaker_or_missing_evidence_keeps_original_three_vote_rule(cfg, case):
    if case == "disabled":
        cfg["policy"]["strong_known_enabled"] = False
    engine = PolicyEngine(cfg)
    for i, now in enumerate((0, 500)):
        observation = (
            obs(B, "KNOWN", pid="person")
            if case == "missing"
            else strong(
                score=0.59 if case == "score" else 0.7, second=0.6 if case == "margin" else None
            )
        )
        engine.ingest(str(i), now, [observation])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    engine.ingest("third", 800, [obs(B, "KNOWN", pid="person")])
    assert engine.tracks["one"].confirmed == "KNOWN"


def test_duplicate_frame_does_not_add_a_strong_vote(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [strong()])
    engine.ingest("one", 500, [strong()])
    assert len(engine.tracks["one"].strong_votes) == 1
    assert engine.tracks["one"].confirmed == "UNCERTAIN"


def test_conflicting_identity_does_not_merge_strong_votes(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [strong(pid="first")])
    engine.ingest("two", 500, [strong(pid="second")])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    assert len(engine.tracks["one"].strong_votes) == 1


def test_unknown_between_strong_knowns_prevents_short_confirmation(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [strong()])
    engine.ingest("unknown", 250, [obs(B)])
    engine.ingest("two", 500, [strong()])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"


def test_unknown_after_short_confirmation_invalidates_final_known(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [strong()])
    assert any(e["reason"] == "known_visit" for e in engine.ingest("two", 500, [strong()]))
    engine.ingest("unknown", 800, [obs(B)])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    final = engine.advance(4000)
    assert final[0]["identity"] == "UNCERTAIN"
    assert not final[0]["audible"]


def test_short_confirmation_does_not_shorten_unknown_alarm_rule(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [obs(A)])
    engine.ingest("two", 500, [obs(A)])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    engine.ingest("third", 800, [obs(B)])
    assert engine.tracks["one"].confirmed == "UNKNOWN"


def test_expired_or_previous_visit_strong_votes_cannot_be_reused(cfg):
    engine = PolicyEngine(cfg)
    engine.ingest("one", 0, [strong()])
    engine.ingest("two", 500, [strong()])
    engine.ingest("expired", 2600, [obs(B, "NO_FACE")])
    assert engine.tracks["one"].confirmed == "UNCERTAIN"
    engine.ingest("a0", 2800, [obs(A, "NO_FACE")])
    engine.ingest("a1", 3100, [obs(A, "NO_FACE")])
    assert not engine.tracks["one"].strong_votes
    engine.ingest("new-b0", 3400, [strong()])
    events = engine.ingest("new-b1", 3700, [obs(B, "NO_FACE")])
    assert not any(e["reason"] == "known_visit" for e in events)


@pytest.mark.parametrize(
    "key,value",
    [
        ("strong_known_min_score", 0.4),
        ("strong_known_min_margin", 0.01),
        ("strong_known_span_ms", 499),
    ],
)
def test_configuration_cannot_weaken_strong_evidence_guards(cfg, key, value):
    cfg["policy"][key] = value
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_strong_confirmation_settings_are_part_of_calibration_hash(cfg):
    before = camera_profile_hash(cfg)
    cfg["policy"]["strong_known_enabled"] = not cfg["policy"]["strong_known_enabled"]
    assert camera_profile_hash(cfg) != before
