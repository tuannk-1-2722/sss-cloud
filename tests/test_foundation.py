import sqlite3
from contextlib import contextmanager

import pytest
from security_app.storage import apply_migrations


def test_migration_upgrade_atomic_and_idempotent(tmp_path):
    (tmp_path / "001_first.sql").write_text("CREATE TABLE example(value INTEGER);")
    db = sqlite3.connect(":memory:")
    apply_migrations(db, tmp_path)
    (tmp_path / "002_second.sql").write_text("ALTER TABLE example ADD COLUMN title TEXT;")
    apply_migrations(db, tmp_path)
    apply_migrations(db, tmp_path)
    assert len(db.execute("PRAGMA table_info(example)").fetchall()) == 2
    (tmp_path / "003_broken.sql").write_text("CREATE TABLE partial(id INTEGER); INVALID SQL;")
    with pytest.raises(sqlite3.Error):
        apply_migrations(db, tmp_path)
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='partial'").fetchone()
    assert db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
    db.close()


def test_newer_schema_is_rejected(tmp_path):
    (tmp_path / "001_first.sql").write_text("CREATE TABLE example(value INTEGER);")
    db = sqlite3.connect(":memory:")
    apply_migrations(db, tmp_path)
    db.execute("INSERT INTO schema_version VALUES(2)")
    with pytest.raises(ValueError, match="schema version"):
        apply_migrations(db, tmp_path)
    db.close()


def test_command_not_exposed_before_transaction_commit(service, monkeypatch):
    original = service.store.connection

    @contextmanager
    def probe():
        with original() as db:
            yield db
            staged = db.execute(
                "SELECT payload_json FROM commands WHERE state='pending'"
            ).fetchall()
            if any("TEST_ALARM" in row[0] for row in staged):
                assert service.command is None or service.command["type"] != "TEST_ALARM"

    monkeypatch.setattr(service.store, "connection", probe)
    service.action("commit-probe-test", "test")
    assert service.command["type"] == "TEST_ALARM"


def test_simulator_rejects_same_generation_different_id():
    from test_device_commands import response

    from tools.device_simulator import AlarmDevice

    d = AlarmDevice()
    d.session = "session"
    d.boot = "boot"
    d.apply(response(), 100)
    assert d.apply(response(command_id="conflicting"), 200)["status"] == "rejected"


def test_simulator_test_rejected_during_preview():
    from test_device_commands import response

    from tools.device_simulator import AlarmDevice

    d = AlarmDevice()
    d.session = "session"
    d.boot = "boot"
    payload = response("TEST_ALARM", mode="DISARMED", duration=1000)
    payload["capture_lease_ms"] = 1000
    assert d.apply(payload, 100)["status"] == "rejected"


def test_command_expires_in_status_even_when_device_stops_syncing(service):
    command = service.action("status-expiry-test", "test")
    with service.lock:
        service.command_deadline = service.now() - 1
    assert service.status()["pending_command"] is None
    with service.store.connection() as db:
        assert (
            db.execute(
                "SELECT state FROM commands WHERE id=?", (command["command_id"],)
            ).fetchone()[0]
            == "expired"
        )
