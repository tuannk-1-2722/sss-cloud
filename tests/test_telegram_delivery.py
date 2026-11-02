"""Provider failures, durable scheduling and privacy; all provider I/O is mocked."""

import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from security_app import telegram
from security_app.notifications import Notifier
from security_app.telegram import configuration


@pytest.fixture
def configured(service, monkeypatch):
    service.cfg["notifications"]["telegram_enabled"] = True
    monkeypatch.setenv("SSS_TELEGRAM_TOKEN", "12345:test-token-private")
    monkeypatch.setenv("SSS_TELEGRAM_CHAT", "123456")
    monkeypatch.setenv("SSS_DASHBOARD_URL", "https://camera.example")
    return service


def enqueue(s, *, reason="unknown_approach", media=None, age_ms=0, severity="alarm"):
    event, outbox = str(uuid4()), str(uuid4())
    created = (datetime.now(timezone.utc) - timedelta(milliseconds=age_ms)).isoformat()
    with s.store.connection() as db:
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,NULL)",
            (event, event, reason, severity, created, "simulator", "{}", media),
        )
        db.execute("INSERT INTO outbox VALUES(?,?,'pending',0,?,NULL)", (outbox, event, created))
    return outbox


def sender(s):
    n = Notifier.__new__(Notifier)
    n.service, n.stop_event = s, threading.Event()
    return n


def row(s, oid):
    with s.store.connection() as db:
        return dict(
            db.execute(
                "SELECT o.*,d.* FROM outbox o LEFT JOIN notification_delivery d ON d.outbox_id=o.id WHERE o.id=?",
                (oid,),
            ).fetchone()
        )


def response(status=200, **body):
    return httpx.Response(status, json=body or {"ok": True, "result": {"message_id": 42}})


def test_disabled_and_missing_credentials_never_call_provider(service, monkeypatch):
    oid = enqueue(service)
    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: pytest.fail("Unexpected provider call"))
    n = sender(service)
    n.step()
    assert row(service, oid)["state"] == "disabled"
    service.cfg["notifications"]["telegram_enabled"] = True
    monkeypatch.delenv("SSS_TELEGRAM_TOKEN", raising=False)
    oid = enqueue(service)
    n.step()
    assert row(service, oid)["state"] == "pending"
    assert service.notification_error == "PROVIDER_NOT_CONFIGURED"


def test_photo_requires_separate_consent_and_sent_does_not_mean_phone_read(configured, monkeypatch):
    s = configured
    s.cfg["notifications"]["telegram_include_photos"] = True
    media = str(uuid4())
    (s.runtime / "media" / f"{media}.jpg").write_bytes(b"synthetic-jpeg")
    monkeypatch.delenv("SSS_TELEGRAM_PHOTO_CONSENT", raising=False)
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append((url, kw)) or response())
    first = enqueue(s, media=media)
    sender(s).step()
    assert calls[-1][0].endswith("sendMessage") and "files" not in calls[-1][1]
    assert configuration(s.cfg["notifications"])["photo_consent_required"]
    monkeypatch.setenv("SSS_TELEGRAM_PHOTO_CONSENT", "1")
    second = enqueue(s, media=media)
    sender(s).step()
    assert calls[-1][0].endswith("sendPhoto")
    assert calls[-1][1]["files"]["photo"][1] == b"synthetic-jpeg"
    assert row(s, second)["provider_message_id"] == 42
    assert row(s, first)["media_kind"] == "text" and row(s, second)["media_kind"] == "photo"
    assert row(s, second)["delivered_at"]


def test_rate_limit_is_durable_global_and_honors_more_than_sixty_seconds(configured, monkeypatch):
    s = configured
    first = enqueue(s)
    calls = []
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_k: (
            calls.append(1) or response(429, ok=False, parameters={"retry_after": 120})
        ),
    )
    sender(s).step()
    delay = datetime.fromisoformat(row(s, first)["next_attempt_at"]) - datetime.now(timezone.utc)
    assert delay.total_seconds() > 110
    second = enqueue(s)
    sender(s).step()
    assert len(calls) == 1 and row(s, second)["attempts"] == 0
    with s.store.connection() as db:
        db.execute("UPDATE notification_runtime SET not_before=NULL")
    sender(
        s
    ).step()  # Other due event is eligible; the first delayed event is not head-of-line blocking.
    assert len(calls) == 2 and row(s, second)["attempts"] == 1


def test_timeout_retries_are_bounded_and_possible_duplicate_is_retained(configured, monkeypatch):
    s = configured
    oid = enqueue(s)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *_a, **_k: (_ for _ in ()).throw(
            httpx.ReadTimeout("private token in a provider exception")
        ),
    )
    n = sender(s)
    for _ in range(s.cfg["notifications"]["max_attempts"]):
        with s.store.connection() as db:
            db.execute("UPDATE notification_delivery SET next_attempt_at=NULL")
        n.step()
    item = row(s, oid)
    assert item["state"] == "unknown" and item["attempts"] == 3 and item["duplicate_possible"] == 1
    n.step()
    assert row(s, oid)["attempts"] == 3


@pytest.mark.parametrize(
    "code,reason", [(401, "BOT_TOKEN_REJECTED"), (403, "BOT_BLOCKED_OR_FORBIDDEN")]
)
def test_permanent_provider_error_is_not_retried(configured, monkeypatch, code, reason):
    oid = enqueue(configured)
    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: response(code, ok=False))
    n = sender(configured)
    n.step()
    n.step()
    item = row(configured, oid)
    assert item["state"] == "failed" and item["attempts"] == 1 and item["reason"] == reason


def test_photo_rejection_falls_back_to_text_but_timeout_does_not(configured, monkeypatch):
    s = configured
    s.cfg["notifications"]["telegram_include_photos"] = True
    monkeypatch.setenv("SSS_TELEGRAM_PHOTO_CONSENT", "1")
    media = str(uuid4())
    (s.runtime / "media" / f"{media}.jpg").write_bytes(b"synthetic-jpeg")
    calls = []

    def provider(url, **kwargs):
        calls.append(url)
        return (
            response(400, ok=False, description="Bad photo dimensions")
            if url.endswith("sendPhoto")
            else response()
        )

    monkeypatch.setattr(httpx, "post", provider)
    oid = enqueue(s, media=media)
    sender(s).step()
    assert len(calls) == 2 and row(s, oid)["state"] == "sent"
    assert row(s, oid)["reason"] == "PHOTO_REJECTED_TEXT_SENT"


def test_expired_queue_does_not_replay_when_enabled(configured, monkeypatch):
    oid = enqueue(configured, age_ms=400000)
    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: pytest.fail("Expired message sent"))
    sender(configured).step()
    assert row(configured, oid)["state"] == "expired"


def test_provider_wait_does_not_block_stop_and_deleted_media_cannot_resurrect(
    configured, monkeypatch
):
    s = configured
    oid = enqueue(s)
    entered, release = threading.Event(), threading.Event()

    def provider(*a, **kw):
        entered.set()
        assert release.wait(3)
        return response()

    monkeypatch.setattr(httpx, "post", provider)
    n = sender(s)
    thread = threading.Thread(target=n.step)
    thread.start()
    try:
        assert entered.wait(2)
        assert s.lock.acquire(timeout=0.2), "Provider I/O held the coordinator lock"
        s.lock.release()
        s.set_mode("DISARMED", s.mode_revision)
        s.deactivate_person(s.test_person_id, delete=True)
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert row(s, oid)["state"] == "unknown"
    assert row(s, oid)["reason"] == "PERSON_DELETED_DURING_DELIVERY"


def test_httpx_logs_redact_bot_token(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info(
            "HTTP POST %s", "https://api.telegram.org/bot12345:private-secret/sendMessage"
        )
    assert "private-secret" not in caplog.text and "[REDACTED]" in caplog.text


def test_malformed_success_is_not_reported_sent(monkeypatch):
    monkeypatch.setenv("SSS_TELEGRAM_TOKEN", "12345:private-token")
    monkeypatch.setenv("SSS_TELEGRAM_CHAT", "123")
    monkeypatch.setattr(httpx, "post", lambda *_a, **_kw: response(200, ok=True, result={}))
    result = telegram.send(
        {
            "event_reason": "notification_test",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "event_id": "test",
        },
        None,
    )
    assert result.state == "unknown"


def test_failed_delivery_commit_is_reconciled_as_uncertain_without_sending_twice(
    configured, monkeypatch
):
    s = configured
    oid = enqueue(s)
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *_a, **_kw: calls.append(1) or response())
    with s.store.connection() as db:
        db.execute(
            "CREATE TRIGGER reject_sent BEFORE UPDATE OF state ON outbox WHEN NEW.state='sent' BEGIN SELECT RAISE(ABORT,'test failure'); END"
        )
    with pytest.raises(Exception, match="test failure"):
        sender(s).step()
    assert row(s, oid)["state"] == "sending"
    sender(s).step()
    assert row(s, oid)["state"] == "unknown" and len(calls) == 1
