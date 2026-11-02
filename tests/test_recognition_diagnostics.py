"""Metadata-only tracing, including discarded in-flight results; no accuracy claims."""

import json
import logging

from fastapi.testclient import TestClient
from security_app.diagnostics import RecognitionDiagnostics
from security_app.main import create_app
from test_api import login


def test_trace_is_bounded_opt_in_and_never_echoes_identity_or_arbitrary_reason(caplog):
    trace = RecognitionDiagnostics(history_limit=2, trace_duration_ms=1000)
    with caplog.at_level(logging.INFO, logger="security_app.diagnostics"):
        trace.record(
            "processed",
            100,
            observations=[
                {
                    "track_id": "private-track",
                    "person_id": "private-person",
                    "decision": "KNOWN",
                    "reason": "private-token",
                    "bbox": [0.1, 0.2, 0.3, 0.4],
                    "embedding": [1, 0],
                }
            ],
        )
        assert not caplog.records
        trace.start(200)
        for now in (300, 400, 1300):
            trace.record(
                "processed",
                now,
                observations=[
                    {
                        "track_id": "private-track",
                        "person_id": "private-person",
                        "decision": "KNOWN",
                        "reason": "private-token",
                        "embedding": [1, 0],
                    }
                ],
            )
    snapshot = trace.snapshot(1300)
    assert len(snapshot["records"]) == 2
    assert not snapshot["trace_active"]
    assert len(caplog.records) == 2
    serialized = json.dumps(snapshot) + caplog.text
    for private in ("private-track", "private-person", "private-token", "embedding"):
        assert private not in serialized
    assert snapshot["records"][-1]["observations"][0]["reason"] == "OTHER"


def test_diagnostics_requires_admin_and_start_requires_csrf(cfg):
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/v2/diagnostics/recognition").status_code == 401
        csrf = login(client)
        assert client.post("/api/v2/diagnostics/recognition/start", json={}).status_code == 403
        started = client.post("/api/v2/diagnostics/recognition/start", headers=csrf, json={})
        assert started.status_code == 200 and started.json()["trace_active"]
        response = client.get("/api/v2/diagnostics/recognition")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert client.delete("/api/v2/diagnostics/recognition", headers=csrf).status_code == 204
        assert not client.get("/api/v2/diagnostics/recognition").json()["trace_active"]
