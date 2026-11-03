"""Durable notification outbox. No provider I/O while holding the coordinator lock."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from .runtime import DomainError
from .telegram import configuration, send
from .telegram import dashboard_link as dashboard_link
from .telegram import telegram_message as telegram_message

logger = logging.getLogger(__name__)


def should_notify(decision):
    """Only the policy's confirmed UNKNOWN event may notify automatically."""
    return decision["reason"] == "unknown_approach" and decision["severity"] == "alarm"


def cancel_ineligible(db, *, push=False):
    """Apply current policy to durable queues, including retries from older releases.

    Explicit admin tests remain available for setup. Completed/uncertain deliveries
    retain their history; cancellation must never claim to recall a sent message.
    """
    table = "push_outbox" if push else "outbox"
    db.execute(
        f"UPDATE {table} SET state='cancelled',reason='NOTIFICATION_POLICY' "
        "WHERE state='pending' AND event_id NOT IN ("
        "SELECT id FROM events WHERE (reason='unknown_approach' AND severity='alarm') "
        "OR (reason='notification_test' AND source='system_test'))"
    )
    if push:
        db.execute(
            "UPDATE push_delivery SET state='cancelled',reason='NOTIFICATION_POLICY' "
            "WHERE state='pending' AND event_id IN ("
            "SELECT event_id FROM push_outbox WHERE state='cancelled' AND reason='NOTIFICATION_POLICY')"
        )


def status(service):
    result = configuration(service.cfg["notifications"])
    with service.store.connection() as db:
        result["counts"] = dict(db.execute("SELECT state,COUNT(*) FROM outbox GROUP BY state"))
        row = db.execute(
            "SELECT o.state,o.reason,d.delivered_at,d.media_kind,d.duplicate_possible "
            "FROM outbox o LEFT JOIN notification_delivery d ON d.outbox_id=o.id "
            "ORDER BY o.created_at DESC,o.rowid DESC LIMIT 1"
        ).fetchone()
        result["latest"] = dict(row) if row else None
    return result


def enqueue_test(service, key, include_photo=False):
    """Explicit operator test, marked system_test. Caller holds coordinator lock."""
    ready = configuration(service.cfg["notifications"])
    if not ready["ready"]:
        raise DomainError(ready["reason"] or "PROVIDER_NOT_CONFIGURED")
    if include_photo and not ready["photos_enabled"]:
        raise DomainError("PHOTO_CONSENT_REQUIRED")
    fingerprint = json.dumps(["notification-test", include_photo])
    with service.store.connection() as db:
        old = db.execute(
            "SELECT fingerprint,response_json FROM action_keys WHERE key=?", (key,)
        ).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise DomainError("IDEMPOTENCY_KEY_REUSED")
            return json.loads(old["response_json"])
        now = datetime.now(timezone.utc)
        recent = db.execute(
            "SELECT 1 FROM events WHERE reason='notification_test' AND created_at>?",
            ((now - timedelta(seconds=30)).isoformat(),),
        ).fetchone()
        if recent:
            raise DomainError("NOTIFICATION_TEST_RATE_LIMIT", 429)
        jpeg = None
        if include_photo:
            latest = service.latest
            if (
                not latest
                or service.now() - latest["received_ms"] + latest.get("capture_age_ms", 0)
                > service.cfg["video"]["stale_ms"]
            ):
                raise DomainError("FRAME_NOT_AVAILABLE", 409)
            jpeg = latest["jpeg"]
        event_id, media_id = str(uuid4()), str(uuid4()) if jpeg else None
        response = {"event_id": event_id, "state": "pending"}
        details = {"test": True, "audible": False, "mode_revision": service.mode_revision}
        try:
            if jpeg:
                temp = service.runtime / "media" / f"{media_id}.tmp"
                temp.write_bytes(jpeg)
                temp.replace(temp.with_suffix(".jpg"))
            db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,NULL)",
                (
                    event_id,
                    f"notification-test-{event_id}",
                    "notification_test",
                    "info",
                    now.isoformat(),
                    "system_test",
                    json.dumps(details),
                    media_id,
                ),
            )
            db.execute(
                "INSERT INTO outbox(id,event_id,state,attempts,created_at) VALUES(?,?,'pending',0,?)",
                (str(uuid4()), event_id, now.isoformat()),
            )
            db.execute(
                "INSERT INTO action_keys VALUES(?,?,?,?)",
                (key, fingerprint, json.dumps(response), now.isoformat()),
            )
        except Exception:
            if media_id:
                for suffix in (".jpg", ".tmp"):
                    (service.runtime / "media" / f"{media_id}{suffix}").unlink(missing_ok=True)
            raise
    return response


class Notifier:
    def __init__(self, service):
        self.service = service
        self.stop_event = threading.Event()
        # A process died during provider I/O: delivery is uncertain, never silently call it sent.
        with service.store.connection() as db:
            db.execute(
                "UPDATE outbox SET state='unknown',reason='PROCESS_INTERRUPTED' WHERE state='sending'"
            )
        self.thread = threading.Thread(target=self.run, name="notification-worker", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=14)
        if self.thread.is_alive():
            # Retain the service/runtime lock until process exit rather than overlap senders.
            raise RuntimeError("NOTIFICATION_WORKER_DID_NOT_STOP")

    def run(self):
        while not self.stop_event.wait(1):
            try:
                self.service.notification_error = None
                self.step()
            except Exception:
                self.service.notification_error = "NOTIFICATION_WORKER_FAULT"
                logger.error("Notification iteration failed; retrying without blocking core")
                self.stop_event.wait(2)

    def step(self):
        s, now = self.service, datetime.now(timezone.utc)
        cfg = s.cfg["notifications"]
        with s.lock, s.store.connection() as db:
            cancel_ineligible(db)
            # Only one sender exists. A leftover claim from a failed previous iteration is uncertain.
            db.execute(
                "UPDATE outbox SET state='unknown',reason='DELIVERY_STATE_LOST' WHERE state='sending'"
            )
            db.execute(
                "UPDATE outbox SET state='expired',reason='ALERT_TOO_OLD' WHERE state='pending' AND created_at<?",
                ((now - timedelta(milliseconds=cfg["expiry_ms"])).isoformat(),),
            )
            if not cfg["telegram_enabled"]:
                db.execute("UPDATE outbox SET state='disabled' WHERE state='pending'")
                return
            ready = configuration(cfg)
            if not ready["ready"]:
                s.notification_error = ready["reason"]
                return
            cooldown = db.execute(
                "SELECT not_before FROM notification_runtime WHERE singleton=1"
            ).fetchone()[0]
            if cooldown and cooldown > now.isoformat():
                return
            row = db.execute(
                "SELECT o.*,e.reason AS event_reason,e.media_id,e.id AS event_id,d.duplicate_possible "
                "FROM outbox o JOIN events e ON o.event_id=e.id "
                "LEFT JOIN notification_delivery d ON d.outbox_id=o.id "
                "WHERE o.state='pending' AND (d.next_attempt_at IS NULL OR d.next_attempt_at<=?) "
                "ORDER BY CASE e.severity WHEN 'alarm' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,o.created_at,o.rowid LIMIT 1",
                (now.isoformat(),),
            ).fetchone()
            if not row:
                return
            row = dict(row)
            if row["attempts"] >= cfg["max_attempts"]:
                db.execute(
                    "UPDATE outbox SET state='unknown',reason='ATTEMPT_LIMIT' WHERE id=?",
                    (row["id"],),
                )
                return
            photo = None
            if ready["photos_enabled"] and row["media_id"]:
                try:
                    UUID(row["media_id"])
                    media = s.runtime / "media"
                    path = media / f"{row['media_id']}.jpg"
                    if path.resolve().is_relative_to(media.resolve()):
                        with path.open("rb") as f:
                            data = f.read(s.cfg["limits"]["max_frame_bytes"] + 1)
                        if 0 < len(data) <= s.cfg["limits"]["max_frame_bytes"]:
                            photo = data
                except (OSError, ValueError):
                    pass  # Deleted/expired media falls back to the event text.
            db.execute(
                "UPDATE outbox SET state='sending',attempts=attempts+1 WHERE id=?", (row["id"],)
            )
            db.execute(
                "INSERT OR IGNORE INTO notification_delivery(outbox_id) VALUES(?)", (row["id"],)
            )
            db.execute(
                "UPDATE notification_delivery SET last_attempt_at=? WHERE outbox_id=?",
                (now.isoformat(), row["id"]),
            )
        if self.stop_event.is_set():
            result_state, reason, result = "unknown", "PROCESS_INTERRUPTED", None
        else:
            result = send(row, photo)  # NO coordinator lock during HTTP or provider backoff.
            result_state, reason = result.state, result.reason
        finished = datetime.now(timezone.utc)
        next_at = None
        if result_state == "retry":
            if row["attempts"] + 1 >= cfg["max_attempts"]:
                result_state = "unknown" if reason == "DELIVERY_UNCONFIRMED" else "failed"
            else:
                result_state = "pending"
                delay = (
                    result.retry_after
                    if result.retry_after is not None
                    else min(30, 2 ** (row["attempts"] + 1))
                )
                # Saturate to representable time; never retry early because of an overflow.
                delay = min(
                    delay,
                    int((datetime.max.replace(tzinfo=timezone.utc) - finished).total_seconds()) - 1,
                )
                next_at = (finished + timedelta(seconds=delay)).isoformat()
        with s.store.connection() as db:
            # Deletion/cancellation during I/O must not be resurrected by a late result.
            changed = db.execute(
                "UPDATE outbox SET state=?,reason=? WHERE id=? AND state='sending'",
                (result_state, reason, row["id"]),
            ).rowcount
            if changed:
                db.execute(
                    "UPDATE notification_delivery SET next_attempt_at=?,delivered_at=?,provider_message_id=?,media_kind=?,"
                    "duplicate_possible=MAX(duplicate_possible,?) WHERE outbox_id=?",
                    (
                        next_at,
                        finished.isoformat() if result_state == "sent" else None,
                        result.message_id if result else None,
                        result.media_kind if result else None,
                        int(reason == "DELIVERY_UNCONFIRMED"),
                        row["id"],
                    ),
                )
            if reason == "RATE_LIMIT" and result and result.retry_after:
                delay = min(
                    result.retry_after,
                    int((datetime.max.replace(tzinfo=timezone.utc) - finished).total_seconds()) - 1,
                )
                db.execute(
                    "UPDATE notification_runtime SET not_before=? WHERE singleton=1",
                    ((finished + timedelta(seconds=delay)).isoformat(),),
                )
        if result_state not in {"sent", "pending"}:
            s.notification_error = reason
