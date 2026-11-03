"""Visit-to-outbox/representative evidence and explicit notification test boundaries."""

import json

import pytest
from fastapi.testclient import TestClient
from security_app.config import camera_profile_hash
from security_app.contracts import FrameMeta
from security_app.main import create_app
from security_app.runtime import Packet
from security_app.telegram import Delivery
from security_app.telegram import configuration as notification_configuration
from test_api import login
from test_policy import A, B, obs


def publish(s, index, observed, observation):
    meta = FrameMeta(
        session_id=s.session_id,
        boot_id=s.boot_id,
        seq=index,
        mode_revision=s.mode_revision,
        purpose="monitoring",
        capture_age_ms=0,
    )
    packet = Packet(
        f"synthetic-{index}",
        meta,
        observed,
        s.gallery_revision,
        jpeg=f"synthetic-frame-{index}".encode(),
        observed_ms=observed,
    )
    s._publish(packet, [observation])


def assert_no_notifications(s):
    with s.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM push_outbox").fetchone()[0] == 0


def test_short_uncertain_completion_keeps_history_without_notifications(service):
    s = service
    s.cfg["notifications"].update(notify_unresolved_visits=True, telegram_include_photos=True)
    s.set_mode("ARMED", s.mode_revision)
    with s.lock:
        start = s.now()
        for i, zone in enumerate((A, A, B, B)):
            publish(s, i, start + i * 300, obs(zone, "NO_FACE"))
        assert len(s.visit_evidence.frames) == 1
        events = s.policy.advance(start + 4000)
        s._commit_decisions(None, events, s.policy.last_alarm)
        with s.store.connection() as db:
            row = db.execute(
                "SELECT reason,media_id,details_json FROM events WHERE reason='visit_completed'"
            ).fetchone()
        assert row["reason"] == "visit_completed"
        assert (s.runtime / "media" / f"{row['media_id']}.jpg").read_bytes() == b"synthetic-frame-3"
        assert json.loads(row["details_json"])["evidence_frame_id"] == "synthetic-3"
        assert not s.visit_evidence.frames
        assert not s.command or s.command["type"] != "START_ALARM"
        assert_no_notifications(s)


def test_known_visit_and_completion_never_notify_even_with_legacy_flags(service):
    s = service
    s.cfg["notifications"].update(
        notify_known_visits=True, notify_unresolved_visits=True, telegram_include_photos=True
    )
    s.set_mode("ARMED", s.mode_revision)
    with s.lock:
        start = s.now()
        for i, zone in enumerate((A, A, A, B, B, A, A)):
            publish(s, i, start + i * 300, obs(zone, "KNOWN", pid=s.test_person_id))
        with s.store.connection() as db:
            reasons = [r[0] for r in db.execute("SELECT reason FROM events ORDER BY rowid")]
        assert reasons == ["known_visit", "visit_completed"]
        assert_no_notifications(s)


def test_unresolved_warning_and_completion_never_notify(service):
    s = service
    s.cfg["notifications"]["notify_unresolved_visits"] = True
    s.set_mode("ARMED", s.mode_revision)
    with s.lock:
        start = s.now()
        for i in range(20):
            publish(s, i, start + i * 300, obs(B, "NO_FACE"))
        s._commit_decisions(None, s.policy.advance(start + 9000), s.policy.last_alarm)
        with s.store.connection() as db:
            reasons = [r[0] for r in db.execute("SELECT reason FROM events ORDER BY rowid")]
        assert reasons == ["unresolved_presence", "visit_completed"]
        assert_no_notifications(s)


def test_only_confirmed_unknown_queues_both_channels_once_with_own_evidence(service):
    s = service
    s.cfg["notifications"].update(notify_known_visits=True, notify_unresolved_visits=True)
    s.set_mode("ARMED", s.mode_revision)
    with s.lock:
        start = s.now()
        publish(s, 0, start, obs(B, "UNKNOWN"))
        assert_no_notifications(s)  # A single identity vote is not a confirmation.
        for i in range(1, 5):
            publish(s, i, start + i * 500, obs(B, "UNKNOWN"))
        s._commit_decisions(None, s.policy.advance(start + 6000), s.policy.last_alarm)
        with s.store.connection() as db:
            for table in ("outbox", "push_outbox"):
                rows = db.execute(
                    f"SELECT e.reason,e.media_id FROM events e JOIN {table} o ON e.id=o.event_id"
                ).fetchall()
                assert len(rows) == 1 and rows[0]["reason"] == "unknown_approach"
                path = s.runtime / "media" / f"{rows[0]['media_id']}.jpg"
                assert path.read_bytes() == b"synthetic-frame-1"


@pytest.mark.parametrize(
    "reason", ["capacity_exceeded", "known_visit", "unresolved_presence", "other"]
)
def test_severity_alone_never_authorizes_notification(service, reason):
    with service.lock:
        service._commit_decisions(
            None,
            [
                {
                    "visit_id": "synthetic-visit",
                    "reason": reason,
                    "severity": "alarm",
                    "audible": False,
                }
            ],
            service.policy.last_alarm,
        )
    assert len(service.store.event_rows()) == 1
    assert_no_notifications(service)


def test_notification_transaction_failure_never_publishes_event_or_either_queue(service):
    import sqlite3

    s = service
    with s.lock:
        with s.store.connection() as db:
            db.execute(
                "CREATE TRIGGER reject_push BEFORE INSERT ON push_outbox "
                "BEGIN SELECT RAISE(ABORT,'synthetic queue failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="synthetic queue failure"):
            s._commit_decisions(
                None,
                [
                    {
                        "visit_id": "synthetic",
                        "reason": "unknown_approach",
                        "severity": "alarm",
                        "audible": True,
                    }
                ],
                s.policy.last_alarm,
            )
        assert s.command is None
        assert s.store.event_rows() == []
        assert_no_notifications(s)


def test_disarm_clears_representative_frames_and_notification_settings_do_not_change_calibration(
    service,
):
    s = service
    before = camera_profile_hash(s.cfg)
    s.cfg["notifications"].update(
        telegram_enabled=True,
        telegram_include_photos=True,
        notify_known_visits=True,
        notify_unresolved_visits=True,
    )
    assert camera_profile_hash(s.cfg) == before
    ready = notification_configuration(s.cfg["notifications"])
    assert ready["known_visits_enabled"] is False
    assert ready["unresolved_visits_enabled"] is False
    s.set_mode("ARMED", s.mode_revision)
    with s.lock:
        for i in range(2):
            publish(s, i, s.now() + i * 300, obs(B, "NO_FACE"))
        assert s.visit_evidence.frames
        s.set_mode("DISARMED", s.mode_revision)
        assert not s.visit_evidence.frames
        s._finish_visits()
        assert s.store.event_rows() == []


def test_admin_notification_test_is_explicit_idempotent_and_never_actuates(cfg, monkeypatch):
    cfg["notifications"]["telegram_enabled"] = True
    monkeypatch.setenv("SSS_TELEGRAM_TOKEN", "123:test-private-token")
    monkeypatch.setenv("SSS_TELEGRAM_CHAT", "12345")
    monkeypatch.setattr(
        "security_app.notifications.send", lambda *_: Delivery("sent", message_id=1)
    )
    with TestClient(create_app(cfg)) as client:
        assert (
            client.post(
                "/api/v2/notifications/test", json={"idempotency_key": "notification-test-1"}
            ).status_code
            == 401
        )
        headers = login(client)
        assert (
            client.post(
                "/api/v2/notifications/test", json={"idempotency_key": "notification-test-1"}
            ).status_code
            == 403
        )
        assert "test-private-token" not in client.get("/api/v2/status").text
        body = {"idempotency_key": "notification-test-1"}
        first = client.post("/api/v2/notifications/test", headers=headers, json=body)
        repeated = client.post("/api/v2/notifications/test", headers=headers, json=body)
        assert first.status_code == 202 and first.json() == repeated.json()
        assert (
            client.post(
                "/api/v2/notifications/test",
                headers=headers,
                json={"idempotency_key": "notification-test-2"},
            ).status_code
            == 429
        )
        s = client.app.state.service
        assert s.mode == "DISARMED" and s.command is None
        event = s.store.event_rows()[0]
        assert event["source"] == "system_test" and event["reason"] == "notification_test"


def test_notification_test_photo_requires_consent_and_a_fresh_frame(cfg, monkeypatch):
    cfg["notifications"].update(telegram_enabled=True, telegram_include_photos=True)
    monkeypatch.setenv("SSS_TELEGRAM_TOKEN", "123:test-private-token")
    monkeypatch.setenv("SSS_TELEGRAM_CHAT", "12345")
    monkeypatch.delenv("SSS_TELEGRAM_PHOTO_CONSENT", raising=False)
    monkeypatch.setattr(
        "security_app.notifications.send", lambda *_: Delivery("sent", message_id=1)
    )
    with TestClient(create_app(cfg)) as client:
        headers = login(client)
        body = {"idempotency_key": "notification-photo-1", "include_photo": True}
        assert (
            client.post("/api/v2/notifications/test", headers=headers, json=body).json()["error"][
                "code"
            ]
            == "PHOTO_CONSENT_REQUIRED"
        )
        monkeypatch.setenv("SSS_TELEGRAM_PHOTO_CONSENT", "1")
        assert (
            client.post("/api/v2/notifications/test", headers=headers, json=body).json()["error"][
                "code"
            ]
            == "FRAME_NOT_AVAILABLE"
        )


def test_failed_test_transaction_removes_its_photo_and_never_sends(cfg, monkeypatch):
    cfg["notifications"].update(telegram_enabled=True, telegram_include_photos=True)
    monkeypatch.setenv("SSS_TELEGRAM_TOKEN", "123:private-token")
    monkeypatch.setenv("SSS_TELEGRAM_CHAT", "12345")
    monkeypatch.setenv("SSS_TELEGRAM_PHOTO_CONSENT", "1")
    monkeypatch.setattr(
        "security_app.notifications.send",
        lambda *_: (_ for _ in ()).throw(AssertionError("No uncommitted delivery")),
    )
    with TestClient(create_app(cfg)) as client:
        headers = login(client)
        s = client.app.state.service
        with s.lock:
            s.video.publish("synthetic", b"synthetic-jpeg", s.now(), s.mode_revision)
            with s.store.connection() as db:
                db.execute(
                    "CREATE TRIGGER reject_notification BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT,'test disk fault'); END"
                )
        reply = client.post(
            "/api/v2/notifications/test",
            headers=headers,
            json={"idempotency_key": "test-rollback-photo", "include_photo": True},
        )
        assert reply.status_code == 503
        assert not list((s.runtime / "media").glob("*.jpg"))
        assert not s.store.event_rows()
