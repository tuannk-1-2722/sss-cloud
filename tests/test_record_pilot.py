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
