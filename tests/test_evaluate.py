import json

import pytest

from tools.evaluate import read_manifest, summarize


def test_manifest_prevents_same_frame_being_used_across_splits(tmp_path):
    (tmp_path / "frame.jpg").write_bytes(b"not a real image: manifest validation only")
    rows = []
    for split in ("validation", "test"):
        rows.append(
            {
                "visit_id": split,
                "participant_alias": "fixture",
                "split": split,
                "kind": "empty",
                "face_eligible": False,
                "frames": [
                    {"path": "frame.jpg", "time_ms": 0},
                    {"path": "frame.jpg", "time_ms": 500},
                ],
            }
        )
    manifest = tmp_path / "visits.jsonl"
    manifest.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="exactly once"):
        read_manifest(manifest)


def test_metrics_keep_unknown_misses_and_ineligible_faces_in_all_visit_denominator():
    base = {
        "kind": "unknown",
        "has_known": False,
        "has_known_candidate": False,
        "correct_known": False,
        "wrong_known": False,
        "has_warning": True,
        "incoming_observed": True,
    }
    report = summarize(
        [
            {**base, "face_eligible": True, "has_alarm_event": True},
            {**base, "face_eligible": False, "has_alarm_event": False},
        ]
    )
    assert report["unknown_alarm_event_recall_all"]["value"] == 0.5
    assert report["unknown_alarm_event_recall_eligible"]["value"] == 1
    assert report["known_correct_visit"]["value"] is None
