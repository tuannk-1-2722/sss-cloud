import os
import time

import pytest
from security_app.config import ensure_runtime_profile
from security_app.contracts import FrameMeta


def test_gallery_rejects_old_preprocessing_even_when_dimension_matches(service):
    with service.store.connection() as db:
        db.execute("UPDATE samples SET preprocess_version='old-version'")
    with pytest.raises(ValueError, match="version/dimension mismatch"):
        service.store.gallery()


def test_simulation_runtime_cannot_be_reused_as_hardware_by_admin_tools(service):
    with pytest.raises(ValueError, match="another profile"):
        ensure_runtime_profile(service.runtime, "hardware")


def test_janitor_removes_old_orphans_with_grace_for_current_write(service):
    media = service.runtime / "media"
    old = [media / "orphan.jpg", media / "partial.tmp"]
    fresh = media / "new.jpg"
    for path in [*old, fresh]:
        path.write_bytes(b"unit fixture")
    for path in old:
        os.utime(path, (time.time() - 7200, time.time() - 7200))
    service.store.cleanup(service.cfg["retention"])
    assert fresh.exists() and all(not p.exists() for p in old)


def test_failed_action_transaction_never_leaves_unpersisted_test_command(service):
    with service.store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_action BEFORE INSERT ON action_keys BEGIN SELECT RAISE(ABORT, 'unit disk failure'); END"
        )
    with pytest.raises(Exception, match="STORAGE_FAULT"):
        service.action("test-failure-0001", "test")
    assert (
        service.command is None or service.command["type"] == "STOP_ALARM"
    ) and service.mode == "DISARMED"
    with service.store.connection() as db:
        assert not db.execute(
            "SELECT 1 FROM commands WHERE state='pending' AND payload_json LIKE '%TEST_ALARM%'"
        ).fetchone()


def test_failed_event_transaction_clears_start_before_sync_can_deliver(service, monkeypatch):
    with service.store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_outbox BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'unit disk failure'); END"
        )
    monkeypatch.setattr(
        service.policy,
        "ingest",
        lambda *_: [
            {
                "visit_id": "unit-visit",
                "reason": "unknown_approach",
                "severity": "alarm",
                "audible": True,
            }
        ],
    )
    service.set_mode("ARMED", service.mode_revision)
    meta = FrameMeta(
        session_id=service.session_id,
        boot_id=service.boot_id,
        seq=1,
        mode_revision=service.mode_revision,
        purpose="monitoring",
        capture_age_ms=0,
    )
    result = service.accept(meta, observations=[])
    for _ in range(100):
        with service.store.connection() as db:
            state = db.execute(
                "SELECT state FROM receipts WHERE frame_id=?", (result["frame_id"],)
            ).fetchone()[0]
        if state == "failed":
            break
        time.sleep(0.01)
    assert state == "failed" and service.mode == "DISARMED"
    assert service.command["type"] == "STOP_ALARM"
    with service.store.connection() as db:
        assert not db.execute("SELECT 1 FROM events").fetchone()
        assert not db.execute(
            "SELECT 1 FROM commands WHERE payload_json LIKE '%START_ALARM%'"
        ).fetchone()


def test_receipt_write_failure_before_inference_does_not_kill_worker(service, monkeypatch):
    original = service._receipt_state

    def fail_processing(frame_id, state, reason=None):
        if state == "processing":
            raise OSError("unit storage failure")
        return original(frame_id, state, reason)

    monkeypatch.setattr(service, "_receipt_state", fail_processing)
    service.set_mode("ARMED", service.mode_revision)
    meta = FrameMeta(
        session_id=service.session_id,
        boot_id=service.boot_id,
        seq=1,
        mode_revision=service.mode_revision,
        purpose="monitoring",
        capture_age_ms=0,
    )
    result = service.accept(meta, observations=[])
    for _ in range(100):
        with service.store.connection() as db:
            state = db.execute(
                "SELECT state FROM receipts WHERE frame_id=?", (result["frame_id"],)
            ).fetchone()[0]
        if state == "failed":
            break
        time.sleep(0.01)
    assert state == "failed" and service.thread.is_alive() and service.mode == "DISARMED"
