from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .notifications import cancel_ineligible, dashboard_link
from .runtime import DomainError

logger = logging.getLogger(__name__)

MAX_SUBSCRIPTIONS = 10
MAX_MEDIA_LINK_SECONDS = 3600
# Browsers only hand out push endpoints on their vendors' push services. Anything else is
# rejected so an authenticated user cannot turn the server into a generic HTTP client.
PUSH_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.apple.com",
    "notify.windows.com",
)
REASONS = {
    "unknown_approach": "CẢNH BÁO: không khớp người đã đăng ký",
    "unresolved_presence": "CẦN KIỂM TRA: chưa đủ căn cứ nhận diện",
    "capacity_exceeded": "CẦN KIỂM TRA: quá nhiều người trong vùng cửa",
    "known_visit": "Người đã đăng ký ghé cửa",
    "visit_completed": "LƯỢT ĐÃ KẾT THÚC: chưa đủ căn cứ xác nhận danh tính",
    "notification_test": "THỬ THÔNG BÁO — không phải phát hiện AI hoặc lệnh còi",
}


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def valid_endpoint(endpoint: str) -> bool:
    if not isinstance(endpoint, str) or not 16 <= len(endpoint) <= 512:
        return False
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return False
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def valid_key(value: str, size: int) -> bool:
    try:
        return isinstance(value, str) and len(b64url_decode(value)) == size
    except ValueError:
        return False


def generate_vapid(runtime: Path) -> str:
    """Create the VAPID key once; the private PEM stays in the runtime directory."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = runtime / "vapid_private.pem"
    if not path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return vapid_public_key(runtime) or ""


def vapid_public_key(runtime: Path) -> str | None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = runtime / "vapid_private.pem"
    if not path.is_file():
        return None
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (ValueError, TypeError):
        return None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        return None
    return b64url(
        key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )


def vapid_subject() -> str | None:
    subject = os.environ.get("SSS_VAPID_SUBJECT", "").strip()
    if len(subject) > 256 or any(c.isspace() for c in subject):
        return None
    if subject.startswith("mailto:") and len(subject) > 7:
        return subject
    parsed = urlsplit(subject)
    return subject if parsed.scheme == "https" and parsed.hostname else None


def push_status(runtime: Path, cfg: dict) -> dict:
    """Public, non-secret push readiness used by the dashboard toggle."""
    n = cfg["notifications"]
    public_key = vapid_public_key(runtime)
    configured = bool(public_key and vapid_subject() and dashboard_link())
    with sqlite3.connect((runtime / "security.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        subscription_count = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        counts = dict(db.execute("SELECT state,COUNT(*) FROM push_outbox GROUP BY state"))
    return {
        "enabled": bool(n["push_enabled"]),
        "configured": configured,
        "public_key": public_key if n["push_enabled"] and configured else None,
        "max_subscriptions": MAX_SUBSCRIPTIONS,
        "subscription_count": subscription_count,
        "delivery_counts": counts,
    }


def enqueue_push_test(service, key):
    """Explicit text-only PWA test; no alarm or Telegram delivery."""
    status = push_status(service.runtime, service.cfg)
    if not status["enabled"] or not status["configured"]:
        raise DomainError("PUSH_DISABLED", 409)
    fingerprint = json.dumps(["push-test"])
    now = datetime.now(timezone.utc)
    with service.lock, service.store.connection() as db:
        old = db.execute(
            "SELECT fingerprint,response_json FROM action_keys WHERE key=?", (key,)
        ).fetchone()
        if old:
            if old["fingerprint"] != fingerprint:
                raise DomainError("IDEMPOTENCY_KEY_REUSED")
            return json.loads(old["response_json"])
        if not status["subscription_count"]:
            raise DomainError("PUSH_NO_SUBSCRIBERS", 409)
        recent = db.execute(
            "SELECT 1 FROM events WHERE reason='notification_test' "
            "AND json_extract(details_json,'$.channel')='push' "
            "AND julianday(created_at)>julianday(?,'-30 seconds')",
            (now.isoformat(),),
        ).fetchone()
        if recent:
            raise DomainError("NOTIFICATION_TEST_RATE_LIMIT", 429)
        event_id = str(uuid4())
        response = {"event_id": event_id, "state": "pending"}
        details = json.dumps({"test": True, "channel": "push", "audible": False})
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,NULL)",
            (
                event_id,
                "push-test-" + event_id,
                "notification_test",
                "info",
                now.isoformat(),
                "system_test",
                details,
                None,
            ),
        )
        db.execute(
            "INSERT INTO push_outbox(event_id,created_at) VALUES(?,?)", (event_id, now.isoformat())
        )
        db.execute(
            "INSERT INTO action_keys VALUES(?,?,?,?)",
            (key, fingerprint, json.dumps(response), now.isoformat()),
        )
    return response


class MediaSigner:
    """Short-lived capability links for notification images (browsers fetch without cookies)."""

    def __init__(self, runtime: Path):
        path = runtime / "push_media.key"
        if not path.exists():
            path.write_text(secrets.token_hex(32))
            try:
                path.chmod(0o600)
            except OSError:
                pass
        self.secret = bytes.fromhex(path.read_text().strip())

    def sign(self, media_id: str, expires: int) -> str:
        return hmac.new(self.secret, f"{media_id}.{expires}".encode(), hashlib.sha256).hexdigest()

    def verify(self, media_id: str, expires: int, signature: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if (
            not isinstance(signature, str)
            or len(signature) != 64
            or any(c not in "0123456789abcdef" for c in signature)
            or not now < expires <= now + MAX_MEDIA_LINK_SECONDS
        ):
            return False
        return hmac.compare_digest(self.sign(media_id, expires), signature)

    def url(self, base: str, media_id: str, ttl_s: int, now: float | None = None) -> str:
        expires = int((time.time() if now is None else now) + ttl_s)
        return f"{base}push-media/{media_id}?exp={expires}&sig={self.sign(media_id, expires)}"


def build_payload(row, cfg: dict, base: str, image_url: str | None) -> dict:
    created = datetime.fromisoformat(row["created_at"]).astimezone()
    payload = {
        "title": REASONS.get(row["event_reason"], "Sự kiện camera vùng cửa"),
        "body": f"Camera cửa chính · {created:%H:%M:%S %d/%m}",
        "event_id": row["event_id"],
        "url": base,
    }
    if image_url:
        payload["image"] = image_url
    return payload


def send_web_push(subscription: dict, payload: dict, private_key: Path, subject: str, ttl: int):
    """Return one of sent / gone / retry / rejected. Never log provider text (capability URLs)."""
    import requests
    from pywebpush import WebPushException, webpush

    session = requests.Session()
    session.trust_env = False
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            vapid_private_key=str(private_key),
            vapid_claims={"sub": subject},
            ttl=ttl,
            timeout=5,
            requests_session=session,
        )
        return "sent"
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            return "gone"
        if status is None or status == 429 or status >= 500:
            return "retry"
        return "rejected"
    except (requests.RequestException, OSError):
        return "retry"
    except (ValueError, TypeError):
        return "rejected"
    finally:
        session.close()


class PushNotifier:
    """Web Push outbox worker. Separate thread, never touches inference, state or GPIO."""

    def __init__(self, service, sender=send_web_push):
        self.service = service
        self.sender = sender
        self.signer = MediaSigner(service.runtime)
        with service.store.connection() as db:
            db.execute(
                "UPDATE push_delivery SET state='unknown',reason='PROCESS_INTERRUPTED' "
                "WHERE state='sending'"
            )
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="push-worker", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=9)  # Finish the bounded 5s HTTP call and short DB write.

    def run(self):
        while not self.stop_event.wait(1):
            try:
                self.step()
                self.service.push_error = None
            except Exception:
                self.service.push_error = "PUSH_WORKER_FAULT"
                logger.error("Push iteration failed; retrying without blocking core")
                self.stop_event.wait(2)

    def step(self):
        cfg = self.service.cfg["notifications"]
        with self.service.store.connection() as db:
            cancel_ineligible(db, push=True)
            row = db.execute(
                "SELECT o.*,e.reason AS event_reason,e.media_id FROM push_outbox o "
                "JOIN events e ON o.event_id=e.id WHERE o.state='pending' "
                "ORDER BY o.created_at LIMIT 1"
            ).fetchone()
        if not row:
            return
        age_ms = (
            datetime.now(timezone.utc) - datetime.fromisoformat(row["created_at"])
        ).total_seconds() * 1000
        runtime = self.service.runtime
        base, subject = dashboard_link(), vapid_subject()
        if not cfg["push_enabled"]:
            state, reason = "disabled", None
        elif age_ms > cfg["expiry_ms"]:
            state, reason = "expired", "ALERT_TOO_OLD"
        elif not (base and subject and vapid_public_key(runtime)):
            state, reason = "failed", "PROVIDER_NOT_CONFIGURED"
        else:
            state, reason = self.deliver(row, cfg, base, subject)
            if state == "pending":
                self.stop_event.wait(min(30, 2 ** min(5, row["attempts"] + 1)))
        with self.service.store.connection() as db:
            db.execute(
                "UPDATE push_outbox SET state=?,reason=?,attempts=attempts+1 WHERE event_id=?",
                (state, reason, row["event_id"]),
            )

    def deliver(self, row, cfg: dict, base: str, subject: str) -> tuple[str, str | None]:
        with self.service.store.connection() as db:
            subs = [dict(r) for r in db.execute("SELECT * FROM push_subscriptions")]
        if not subs:
            return "no_subscribers", None
        image_url = None
        if cfg["push_include_image"] and row["media_id"]:
            media = self.service.runtime / "media" / f"{row['media_id']}.jpg"
            if media.is_file():
                image_url = self.signer.url(base, row["media_id"], cfg["push_image_ttl_s"])
        payload = build_payload(row, cfg, base, image_url)
        ttl = max(1, cfg["expiry_ms"] // 1000)
        key = self.service.runtime / "vapid_private.pem"
        outcomes = []
        for sub in subs:
            if self.stop_event.is_set():
                return "pending", "SHUTDOWN"
            with self.service.store.connection() as db:
                previous = db.execute(
                    "SELECT state,attempts FROM push_delivery WHERE event_id=? AND subscription_id=?",
                    (row["event_id"], sub["id"]),
                ).fetchone()
                if previous and previous["state"] in {"sent", "gone", "failed", "unknown"}:
                    outcomes.append(previous["state"])
                    continue
                if previous and previous["state"] == "sending":
                    outcomes.append("unknown")
                    db.execute(
                        "UPDATE push_delivery SET state='unknown',reason='PROCESS_INTERRUPTED' "
                        "WHERE event_id=? AND subscription_id=?",
                        (row["event_id"], sub["id"]),
                    )
                    continue
                attempts = (previous["attempts"] if previous else 0) + 1
                db.execute(
                    "INSERT INTO push_delivery(event_id,subscription_id,state,attempts) "
                    "VALUES(?,?,'sending',?) ON CONFLICT(event_id,subscription_id) "
                    "DO UPDATE SET state='sending',attempts=excluded.attempts,reason=NULL",
                    (row["event_id"], sub["id"], attempts),
                )
            info = {
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            }
            outcome = self.sender(info, payload, key, subject, ttl)
            delivery = (
                "pending"
                if outcome == "retry" and attempts < cfg["max_attempts"]
                else "failed"
                if outcome not in {"sent", "gone"}
                else outcome
            )
            outcomes.append(delivery)
            with self.service.store.connection() as db:
                db.execute(
                    "UPDATE push_delivery SET state=?,reason=? WHERE event_id=? AND subscription_id=?",
                    (
                        delivery,
                        "DELIVERY_UNCONFIRMED" if outcome == "retry" else None,
                        row["event_id"],
                        sub["id"],
                    ),
                )
                if outcome == "gone":
                    db.execute("DELETE FROM push_subscriptions WHERE id=?", (sub["id"],))
        if "pending" in outcomes:
            return "pending", "DELIVERY_UNCONFIRMED"
        if outcomes and all(value == "sent" for value in outcomes):
            return "sent", None
        if "sent" in outcomes:
            return "partial", "SOME_DEVICES_NOT_CONFIRMED"
        if "unknown" in outcomes:
            return "unknown", "DELIVERY_UNCONFIRMED"
        return "failed", "PROVIDER_REJECTED"


def add_subscription(store, endpoint: str, p256dh: str, auth: str) -> bool:
    """Return False when a new subscription would exceed the cap."""
    with store.connection() as db:
        exists = db.execute(
            "SELECT 1 FROM push_subscriptions WHERE endpoint=?", (endpoint,)
        ).fetchone()
        count = db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]
        if not exists and count >= MAX_SUBSCRIPTIONS:
            return False
        db.execute(
            "INSERT INTO push_subscriptions(id,endpoint,p256dh,auth,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh,auth=excluded.auth",
            (str(uuid4()), endpoint, p256dh, auth, datetime.now(timezone.utc).isoformat()),
        )
    return True


def remove_subscription(store, endpoint: str):
    with store.connection() as db:
        db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
