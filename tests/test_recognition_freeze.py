"""Guard the operator-tested recognition implementation while integrating PWA and alarm policy."""

import hashlib
import inspect
import json

from security_app.config import ROOT
from security_app.policy import PolicyEngine


def test_demo_recognition_still_matches_operator_approved_freeze(cfg):
    freeze = json.loads((ROOT / "configs/recognition.freeze.json").read_text())
    assert cfg["vision"] == freeze["vision"]
    assert {key: cfg["policy"][key] for key in freeze["identity_policy"]} == freeze[
        "identity_policy"
    ]
    for name, wanted in freeze["source_sha256"].items():
        assert (
            hashlib.sha256((ROOT / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == wanted
        )
    assert (
        hashlib.sha256(inspect.getsource(PolicyEngine._vote).encode()).hexdigest()
        == freeze["identity_vote_sha256"]
    )
