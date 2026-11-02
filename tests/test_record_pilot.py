"""Recorder consent and clock handling using synthetic bytes; no real images."""

import json
import sys

import pytest

from tools import record_pilot, record_visit


def test_pilot_requires_consent_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["record_pilot", "--server", "https://camera.example", "--directory", str(tmp_path)],
    )
    monkeypatch.setattr(
        record_pilot.httpx, "Client", lambda **_: pytest.fail("Network before consent")
    )
    with pytest.raises(SystemExit) as result:
        record_pilot.main()
    assert result.value.code == 2


def test_capture_uses_source_timestamps_and_releases_view(monkeypatch, tmp_path):
    clock = [0.0]
    monkeypatch.setattr(record_visit.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        record_visit.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class Response:
        status_code = 200
        content = b"synthetic-image-bytes"

        def raise_for_status(self):
            pass

        def json(self):
            return {"lease_id": "view"}

    class Client:
        seq = 0
        released = False

        def post(self, *_, **__):
            return Response()

        def get(self, *_):
            self.seq += 1
            response = Response()
            observed = 1000 + self.seq * 250
            response.headers = {
                "X-Frame-Id": str(self.seq),
                "X-Observed-Ms": str(observed),
                "X-Received-Ms": str(observed + 300),
            }
            return response

        def delete(self, path):
            self.released = path == "video/viewers/view"

    client = Client()
    result = record_visit.capture_visit(
        client,
        directory=tmp_path,
        seconds=1,
        participant_alias="P01",
        split="validation",
        kind="known",
        condition="mask",
        expected_person_id="private-fixture",
    )
    assert client.released and result["timing_basis"] == "server_estimate"
    assert result["frames"][0]["time_ms"] == 0 and result["frames"][0]["received_ms"] == 300
    assert len(result["frames"]) == 4
    row = json.loads((tmp_path / "visits.jsonl").read_text())
    assert row["condition"] == "mask"
    assert all((tmp_path / frame["path"]).is_file() for frame in row["frames"])


def recording_camera(monkeypatch, available):
    clock = [0.0]
    monkeypatch.setattr(record_visit.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        record_visit.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class Response:
        content = b"synthetic-image-bytes"

        def __init__(self, status=200):
            self.status_code = status
            stamp = int(clock[0] * 1000)
            self.headers = {
                "X-Frame-Id": str(stamp),
                "X-Observed-Ms": str(stamp),
                "X-Received-Ms": str(stamp + 100),
            }

        def json(self):
            return {"lease_id": "view"}

        def raise_for_status(self):
            assert self.status_code == 200

    class Client:
        released = False
        renewals = 0

        def get(self, *_):
            return Response(200 if available(clock[0]) else 404)

        def post(self, *_, json):
            self.renewals += bool(json.get("lease_id"))
            return Response()

        def delete(self, *_):
            self.released = True

    return Client()


def test_cold_stream_does_not_consume_visit_and_lease_renews_during_preparation(
    monkeypatch, tmp_path
):
    client = recording_camera(monkeypatch, lambda now: now >= 3)
    result = record_visit.capture_visit(
        client,
        directory=tmp_path,
        seconds=2,
        participant_alias="P01",
        split="validation",
        kind="known",
        condition="clear",
        expected_person_id="fixture",
        ready_timeout_s=10,
        countdown_s=5,
        require_coverage=True,
    )
    assert len(result["frames"]) == 8
    assert result["frames"][-1]["time_ms"] == 1750
    assert client.released and client.renewals >= 2


def test_incomplete_clip_is_retained_for_diagnosis_but_not_added_to_manifest(monkeypatch, tmp_path):
    client = recording_camera(monkeypatch, lambda now: now < 0.5)
    with pytest.raises(RuntimeError, match="Incomplete recording"):
        record_visit.capture_visit(
            client,
            directory=tmp_path,
            seconds=2,
            participant_alias="P01",
            split="validation",
            kind="known",
            condition="clear",
            expected_person_id="fixture",
            require_coverage=True,
        )
    assert client.released and not (tmp_path / "visits.jsonl").exists()
    health = json.loads(next(tmp_path.glob("*/capture-health.json")).read_text())
    assert health["unique_frames"] == 2 and health["client_coverage_ms"] == 250
    assert health["missing_frame_polls"] == 6
    assert not health["coverage_sufficient"]
    partial = json.loads(next(tmp_path.glob("*/visit.partial.json")).read_text())
    assert partial["timing_basis"] == "server_estimate"
    assert [f["time_ms"] for f in partial["frames"]] == [0, 250]
    assert [f["received_ms"] for f in partial["frames"]] == [100, 350]


def test_warmup_timeout_releases_view_without_recording(monkeypatch, tmp_path):
    client = recording_camera(monkeypatch, lambda _: False)
    with pytest.raises(RuntimeError, match="did not stabilize"):
        record_visit.capture_visit(
            client,
            directory=tmp_path,
            seconds=2,
            participant_alias="P01",
            split="validation",
            kind="known",
            condition="clear",
            expected_person_id="fixture",
            ready_timeout_s=1,
        )
    assert client.released and not (tmp_path / "visits.jsonl").exists()
    assert not list(tmp_path.rglob("*.jpg"))
