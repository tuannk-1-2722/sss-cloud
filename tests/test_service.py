import time

import pytest
from security_app.contracts import FrameMeta, Observation, Sync
from security_app.service import DomainError


def metadata(service, seq):
    return FrameMeta(
        session_id=service.session_id,
        boot_id=service.boot_id,
        seq=seq,
        mode_revision=service.mode_revision,
        purpose="monitoring",
        capture_age_ms=0,
    )


def wait_processed(service, frame_id):
    for _ in range(100):
        with service.store.connection() as db:
            row = db.execute("SELECT state FROM receipts WHERE frame_id=?", (frame_id,)).fetchone()
        if row["state"] not in {"accepted", "processing"}:
            return row["state"]
        time.sleep(0.01)
    raise AssertionError("worker did not process frame within 1s")


def test_same_frame_retry_no_reprocessing_and_changed_payload_conflicts(service):
    service.set_mode("ARMED", service.mode_revision)
    meta = metadata(service, 1)
    result = service.accept(meta, observations=[])
    assert wait_processed(service, result["frame_id"]) == "processed"
    duplicate = service.accept(meta, observations=[])
    assert duplicate["duplicate"] and duplicate["frame_id"] == result["frame_id"]
    with pytest.raises(DomainError, match="FRAME_ID_REUSED"):
        service.accept(
            meta,
            observations=[Observation(track_id="x", bbox=(0.1, 0.1, 0.1, 0.1), decision="UNKNOWN")],
        )


def test_receipt_purge_watermark_still_blocks_replay(service):
    service.set_mode("ARMED", service.mode_revision)
    meta = metadata(service, 1)
    result = service.accept(meta, observations=[])
    wait_processed(service, result["frame_id"])
    with service.store.connection() as db:
        db.execute("DELETE FROM receipts")
    with pytest.raises(DomainError, match="STALE_FRAME_SEQUENCE"):
        service.accept(meta, observations=[])


def test_disarm_invalidates_old_revision_and_commands(service):
    service.set_mode("ARMED", service.mode_revision)
    old = metadata(service, 1)
    service.set_mode("DISARMED", service.mode_revision)
    with pytest.raises(DomainError, match="MODE_REVISION_CONFLICT"):
        service.accept(old, observations=[])
    assert service.command["type"] == "STOP_ALARM"


def test_idempotent_test_and_stop_supersedes_start(service):
    first = service.action("test-key-0001", "test")
    second = service.action("test-key-0001", "test")
    assert first == second
    service.set_mode("DISARMED", service.mode_revision)
    with service.store.connection() as db:
        row = db.execute("SELECT state FROM commands WHERE id=?", (first["command_id"],)).fetchone()
    assert row["state"] == "superseded"


def test_sync_retry_does_not_extend_command_lease(service):
    service.action("test-key-0002", "test")
    request = Sync(session_id=service.session_id, boot_id=service.boot_id, sync_seq=1, uptime_ms=0)
    first = service.sync(request)
    service.command_deadline -= 1000
    second = service.sync(request)
    assert second["command"]["remaining_ttl_ms"] <= first["command"]["remaining_ttl_ms"] - 1000


def test_deletion_during_inference_discards_old_snapshot(service):
    # Hold the service lock so pending cannot start, then invalidate its gallery revision.
    service.set_mode("ARMED", service.mode_revision)
    with service.lock:
        meta = metadata(service, 1)
        packet = service.accept(meta, observations=[])
        service.deactivate_person(service.test_person_id)
    assert wait_processed(service, packet["frame_id"]) == "cancelled"
    assert not service.store.event_rows()


def test_hardware_refuses_synthetic_observations(service):
    service.cfg["profile"] = "hardware"
    with pytest.raises(DomainError, match="SYNTHETIC_INPUT_DISABLED"):
        service.accept(metadata(service, 1), observations=[])
