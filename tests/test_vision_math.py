import numpy as np
import pytest
from security_app.config import validate_config
from security_app.matching import match, normalize
from security_app.vision import ConservativeTracker, FaceRecord, OpenCVVision, preprocess_image


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


def test_ai_preprocessor_preserves_only_actual_resize_scale():
    image = np.full((240, 320, 3), 96, dtype=np.uint8)
    processed, scale = preprocess_image(image)
    assert processed.shape == (360, 480, 3)
    assert scale == 1.5


def test_insightface_embedding_fits_gallery_contract():
    vision = OpenCVVision.__new__(OpenCVVision)
    vision.dimension = 512
    vision.last_timings = {}
    embedding = np.eye(1, 512, dtype=np.float32).reshape(-1)
    vector = vision.feature(
        FaceRecord(values=np.zeros(14, dtype=np.float32), source=None, embedding=embedding)
    )
    assert vector.shape == (512,)
    assert np.isclose(np.linalg.norm(vector), 1)


def test_face_detector_does_not_compute_embedding_before_zone_policy(monkeypatch, cfg):
    class Detector:
        def detect(self, *_args, **_kwargs):
            return (
                np.array([[100, 100, 180, 180, 0.99]], dtype=np.float32),
                np.array([[[120, 130], [160, 130], [140, 150], [125, 165], [155, 165]]]),
            )

    class Recognition:
        def get(self, *_args):
            raise AssertionError("Recognition runs only after zone and quality checks")

    class Face:
        def __init__(self, **values):
            self.__dict__.update(values)

    vision = OpenCVVision.__new__(OpenCVVision)
    vision.face_analysis = type(
        "FaceAnalysisDouble",
        (),
        {"models": {"detection": Detector(), "recognition": Recognition()}},
    )()
    vision.face_type = Face
    vision.last_timings = {}
    records = vision.faces(np.full((480, 640, 3), 128, dtype=np.uint8))
    assert len(records) == 1
    assert records[0].embedding is None
