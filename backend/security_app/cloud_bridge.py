"""Opt-in metadata-only event replication; failures must not affect the local alarm."""

from __future__ import annotations

import logging
import threading

import httpx

from .cloud_remote import cloud_credentials

logger = logging.getLogger(__name__)


class CloudBridge:
    def __init__(self, service):
        self.service = service
        self.stop_event = threading.Event()
        self.thread = None
        if service.cfg["cloud"]["events_enabled"]:
            self.url, self.token = cloud_credentials()
            self.thread = threading.Thread(target=self.run, daemon=True, name="cloud-events")
            self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=4)

    def run(self):
        while not self.stop_event.wait(1):
            try:
                self.step()
                self.service.cloud_error = None
            except Exception:
                # Network errors and headers may include credentials; log only a fixed code.
                self.service.cloud_error = "CLOUD_EVENT_SYNC_FAULT"
                logger.warning("Cloud event delivery pending; local service continues")
                self.stop_event.wait(5)

    def step(self):
        with self.service.store.connection() as db:
            row = db.execute(
                "SELECT c.event_id,c.attempts,e.reason,e.severity,e.created_at,e.source "
                "FROM cloud_outbox c JOIN events e ON e.id=c.event_id "
                "WHERE c.state='pending' ORDER BY e.created_at LIMIT 1"
            ).fetchone()
        if row is None:
            return
        payload = {
            "event_id": row["event_id"],
            "device_id": self.service.cfg["device_id"],
            "reason": row["reason"],
            "severity": row["severity"],
            "occurred_at": row["created_at"],
            "source": row["source"],
        }
        with httpx.Client(timeout=4, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                f"{self.url}/v1/events",
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
            )
        if response.status_code not in (200, 201):
            raise ValueError("CLOUD_EVENT_REJECTED")
        with self.service.store.connection() as db:
            db.execute(
                "UPDATE cloud_outbox SET state='sent', attempts=attempts+1 WHERE event_id=?",
                (row["event_id"],),
            )
