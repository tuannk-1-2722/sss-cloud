import pytest
from security_app.config import validate_config
from security_app.matching import match, normalize
from security_app.vision import ConservativeTracker


def test_empty_gallery_does_not_call_everyone_unknown(cfg):
    assert match([1, 0], {}, cfg["vision"])["decision"] == "UNCERTAIN"


def test_distinct_identity_margin_is_required(cfg):
    gallery = {"a": [normalize([1, 0])], "b": [normalize([0.99, 0.01])]}
    assert match([1, 0], gallery, cfg["vision"])["decision"] == "UNCERTAIN"
    assert match([-1, 0], gallery, cfg["vision"])["decision"] == "UNKNOWN"


@pytest.mark.parametrize("value", [[0, 0], [float("nan"), 1], [float("inf"), 1]])
def test_nonfinite_or_zero_embeddings_rejected(value):
    with pytest.raises(ValueError):
        normalize(value)


def test_crossing_ambiguity_breaks_continuity(cfg):
    tracker = ConservativeTracker(cfg)
    ids = tracker.assign([(0.1, 0.1, 0.3, 0.6), (0.2, 0.1, 0.3, 0.6)], 0)
    new = tracker.assign([(0.15, 0.1, 0.3, 0.6)], 500)
    assert not set(ids).intersection(new)


def test_face_only_cannot_reuse_body_geometry(cfg):
    tracker = ConservativeTracker(cfg)
    old = tracker.assign([(0.1, 0.1, 0.3, 0.6)], 0, [True])
    new = tracker.assign([(0.1, 0.1, 0.3, 0.6)], 500, [False])
    assert old != new


def test_invalid_configuration_fails_before_run(cfg):
    cfg["zones"]["doorstep"] = cfg["zones"]["approach"]
    with pytest.raises(ValueError):
        validate_config(cfg)
