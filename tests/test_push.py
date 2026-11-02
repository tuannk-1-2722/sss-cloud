"""Web Push: allowlist, signed image links, outbox worker and subscription API (synthetic only)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from security_app.contracts import FrameMeta
from security_app.main import create_app
from security_app.push import (
    MAX_SUBSCRIPTIONS,
    MediaSigner,
    PushNotifier,
    add_subscription,
    b64url,
    generate_vapid,
    valid_endpoint,
    vapid_public_key,
    vapid_subject,
)

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc-def-0123456789"
P256DH = b64url(b"\x04" + os.urandom(64))
AUTH = b64url(os.urandom(16))
JPEG = b"\xff\xd8\xff\xd9"


@pytest.fixture
def push_env(monkeypatch):
    monkeypatch.setenv("SSS_DASHBOARD_URL", "https://home.example.org")
    monkeypatch.setenv("SSS_VAPID_SUBJECT", "mailto:team@example.org")


@pytest.fixture
def client(cfg, push_env):
    cfg["notifications"]["push_enabled"] = True
    generate_vapid(__import__("pathlib").Path(cfg["runtime_dir"]))
    with TestClient(create_app(cfg)) as c:
        yield c


def login(client):
    response = client.post(
        "/api/v2/auth/login", json={"username": "admin", "password": "test-password-private"}
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def body(endpoint=ENDPOINT):
    return {"endpoint": endpoint, "keys": {"p256dh": P256DH, "auth": AUTH}}


def add_event(service, media=False, age_s=0, reason="unknown_approach"):
    event_id, media_id = str(uuid4()), str(uuid4()) if media else None
    created = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    if media:
        (service.runtime / "media" / f"{media_id}.jpg").write_bytes(JPEG)
    with service.store.connection() as db:
        db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,NULL)",
            (event_id, str(uuid4()), reason, "alarm", created, "simulator", "{}", media_id),
        )
        db.execute("INSERT INTO push_outbox(event_id,created_at) VALUES(?,?)", (event_id, created))
    return event_id, media_id


def outbox(service, event_id):
    with service.store.connection() as db:
        return dict(
            db.execute("SELECT * FROM push_outbox WHERE event_id=?", (event_id,)).fetchone()
        )


def worker(service, sender):
    notifier = PushNotifier.__new__(PushNotifier)
    notifier.service = service
    notifier.sender = sender
    notifier.signer = MediaSigner(service.runtime)
    notifier.stop_event = SimpleNamespace(wait=lambda *_: False, is_set=lambda: False)
    return notifier


@pytest.fixture
def ready_service(service, push_env):
    service.cfg["notifications"]["push_enabled"] = True
    generate_vapid(service.runtime)
    return service


@pytest.mark.parametrize(
    "endpoint,ok",
    [
        (ENDPOINT, True),
        ("https://updates.push.services.mozilla.com/wpush/v2/abcdefgh", True),
        ("https://web.push.apple.com/QabcdefghijKLM", True),
        ("https://wns2-par02p.notify.windows.com/w/?token=abcdefgh", True),
        ("http://fcm.googleapis.com/fcm/send/abcdefgh", False),
        ("https://evil.example.org/fcm.googleapis.com/abcdefgh", False),
        ("https://fcm.googleapis.com.evil.org/send/abcdefgh", False),
        ("https://user:pw@fcm.googleapis.com/fcm/send/abcdefgh", False),
        ("https://fcm.googleapis.com:8443/fcm/send/abcdefgh", False),
        ("https://127.0.0.1/abcdefghijklmnop", False),
        ("https://fcm.googleapis.com/" + "a" * 600, False),
    ],
)
def test_endpoint_allowlist(endpoint, ok):
    assert valid_endpoint(endpoint) is ok


def test_vapid_key_is_stable_and_subject_validated(tmp_path, monkeypatch):
    first = generate_vapid(tmp_path)
    assert first == generate_vapid(tmp_path) == vapid_public_key(tmp_path)
    assert len(first) == 87
    for value, expected in [
        ("mailto:a@b.org", "mailto:a@b.org"),
        ("https://b.org", "https://b.org"),
        ("http://b.org", None),
        ("mailto:", None),
        ("", None),
    ]:
        monkeypatch.setenv("SSS_VAPID_SUBJECT", value)
        assert vapid_subject() == expected


def test_media_link_expires_and_rejects_tampering(tmp_path):
    signer = MediaSigner(tmp_path)
    now = 1_000_000
    exp = now + 600
    sig = signer.sign("m1", exp)
    assert signer.verify("m1", exp, sig, now)
    assert not signer.verify("m1", exp, sig, exp)
    assert not signer.verify("m2", exp, sig, now)
    assert not signer.verify("m1", exp + 1, sig, now)
    assert not signer.verify("m1", exp, sig[:-1] + ("0" if sig[-1] != "0" else "1"), now)
    far = now + 86400
    assert not signer.verify("m1", far, signer.sign("m1", far), now)
    assert MediaSigner(tmp_path).verify("m1", exp, sig, now)


def test_subscription_api_requires_login_csrf_and_config(client):
    assert client.get("/api/v2/push/config").status_code == 401
    assert client.post("/api/v2/push/subscribe", json=body()).status_code == 401
    headers = login(client)
    assert client.post("/api/v2/push/subscribe", json=body()).status_code == 403
    config = client.get("/api/v2/push/config").json()
    assert config["enabled"] and config["configured"] and len(config["public_key"]) == 87
    assert "private" not in str(config).lower()
    assert client.post("/api/v2/push/subscribe", json=body(), headers=headers).status_code == 201
    assert client.post("/api/v2/push/subscribe", json=body(), headers=headers).status_code == 201
    with client.app.state.service.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 1
    response = client.post("/api/v2/push/unsubscribe", json={"endpoint": ENDPOINT}, headers=headers)
    assert response.status_code == 204
    with client.app.state.service.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0


def test_subscribe_rejects_bad_endpoint_keys_and_limit(client):
    headers = login(client)
    post = lambda payload: client.post("/api/v2/push/subscribe", json=payload, headers=headers)  # noqa: E731
    assert post(body("https://evil.example.org/x" + "a" * 20)).status_code == 422
    bad = body()
    bad["keys"]["auth"] = b64url(os.urandom(21))
    assert post(bad).status_code == 422
    short = body()
    short["keys"]["p256dh"] = b64url(os.urandom(20))
    assert post(short).status_code == 422
    for i in range(MAX_SUBSCRIPTIONS):
        assert post(body(ENDPOINT + str(i))).status_code == 201
    assert post(body(ENDPOINT + "overflow")).status_code == 409


def test_subscribe_fails_closed_when_disabled_or_unconfigured(cfg, monkeypatch):
    monkeypatch.delenv("SSS_DASHBOARD_URL", raising=False)
    monkeypatch.delenv("SSS_VAPID_SUBJECT", raising=False)
    with TestClient(create_app(cfg)) as client:
        headers = login(client)
        assert client.get("/api/v2/push/config").json()["public_key"] is None
        assert (
            client.post("/api/v2/push/subscribe", json=body(), headers=headers).status_code == 409
        )
    cfg["notifications"]["push_enabled"] = True
    with TestClient(create_app(cfg)) as client:
        headers = login(client)
        config = client.get("/api/v2/push/config").json()
        assert config["enabled"] and not config["configured"] and config["public_key"] is None
        assert (
            client.post("/api/v2/push/subscribe", json=body(), headers=headers).status_code == 409
        )


def test_push_media_needs_valid_unexpired_signature(client):
    service = client.app.state.service
    _, media_id = add_event(service, media=True)
    signer = client.app.state.media_signer
    exp = int(time.time()) + 300
    good = f"/push-media/{media_id}?exp={exp}&sig={signer.sign(media_id, exp)}"
    response = client.get(good)  # deliberately no login cookie
    assert response.status_code == 200 and response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(f"/push-media/{media_id}?exp={exp}&sig={'0' * 64}").status_code == 404
    old = int(time.time()) - 5
    assert (
        client.get(f"/push-media/{media_id}?exp={old}&sig={signer.sign(media_id, old)}").status_code
        == 404
    )
    other = str(uuid4())
    assert (
        client.get(f"/push-media/{other}?exp={exp}&sig={signer.sign(other, exp)}").status_code
        == 404
    )
    traversal = "..%5Csecurity.sqlite3"
    assert (
        client.get(
            f"/push-media/{traversal}?exp={exp}&sig={signer.sign(traversal, exp)}"
        ).status_code
        == 404
    )
    service.cfg["notifications"]["push_enabled"] = False
    assert client.get(good).status_code == 404


def test_worker_sends_signed_image_and_never_embeds_jpeg(ready_service):
    add_subscription(ready_service.store, ENDPOINT, P256DH, AUTH)
    event_id, media_id = add_event(ready_service, media=True)
    sent = []

    def sender(info, payload, key, subject, ttl):
        sent.append((info, payload, subject, ttl))
        return "sent"

    worker(ready_service, sender).step()
    assert outbox(ready_service, event_id)["state"] == "sent"
    info, payload, subject, ttl = sent[0]
    assert info["endpoint"] == ENDPOINT and subject == "mailto:team@example.org" and ttl == 300
    assert payload["event_id"] == event_id and payload["url"] == "https://home.example.org/"
    assert payload["image"].startswith(f"https://home.example.org/push-media/{media_id}?exp=")
    assert "CẢNH BÁO" in payload["title"] and len(str(payload)) < 1000


def test_worker_omits_image_when_disabled_or_missing(ready_service):
    add_subscription(ready_service.store, ENDPOINT, P256DH, AUTH)
    payloads = []
    sender = lambda info, payload, *_: payloads.append(payload) or "sent"  # noqa: E731
    add_event(ready_service, media=False)
    worker(ready_service, sender).step()
    ready_service.cfg["notifications"]["push_include_image"] = False
    add_event(ready_service, media=True)
    worker(ready_service, sender).step()
    assert len(payloads) == 2 and all("image" not in p for p in payloads)


def test_worker_removes_gone_subscription_and_marks_failure(ready_service):
    add_subscription(ready_service.store, ENDPOINT, P256DH, AUTH)
    event_id, _ = add_event(ready_service)
    worker(ready_service, lambda *_: "gone").step()
    assert outbox(ready_service, event_id)["state"] == "failed"
    with ready_service.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0
    event_id, _ = add_event(ready_service)
    worker(ready_service, lambda *_: "sent").step()
    assert outbox(ready_service, event_id)["state"] == "no_subscribers"


def test_worker_retry_is_bounded(ready_service):
    add_subscription(ready_service.store, ENDPOINT, P256DH, AUTH)
    event_id, _ = add_event(ready_service)
    calls = []
    notifier = worker(ready_service, lambda *a: calls.append(a) or "retry")
    for expected in ("pending", "pending", "failed"):
        notifier.step()
        assert outbox(ready_service, event_id)["state"] == expected
    notifier.step()
    assert len(calls) == ready_service.cfg["notifications"]["max_attempts"]


def test_worker_one_success_is_enough_and_not_retried(ready_service):
    add_subscription(ready_service.store, ENDPOINT, P256DH, AUTH)
    add_subscription(ready_service.store, ENDPOINT + "-2", P256DH, AUTH)
    event_id, _ = add_event(ready_service)
    results = iter(["sent", "retry"])
    worker(ready_service, lambda *_: next(results)).step()
    assert outbox(ready_service, event_id)["state"] == "sent"


@pytest.mark.parametrize(
    "mutate,age,state,reason",
    [
        (lambda s, m: s.cfg["notifications"].update(push_enabled=False), 0, "disabled", None),
        (lambda s, m: None, 301, "expired", "ALERT_TOO_OLD"),
        (lambda s, m: m.delenv("SSS_VAPID_SUBJECT"), 0, "failed", "PROVIDER_NOT_CONFIGURED"),
        (lambda s, m: m.delenv("SSS_DASHBOARD_URL"), 0, "failed", "PROVIDER_NOT_CONFIGURED"),
        (
            lambda s, m: (s.runtime / "vapid_private.pem").unlink(),
            0,
            "failed",
            "PROVIDER_NOT_CONFIGURED",
        ),
    ],
)
def test_worker_fails_closed_without_calling_provider(
    ready_service, monkeypatch, mutate, age, state, reason
):
    add_subscription(ready_service.store, ENDPOINT, P256DH, AUTH)
    event_id, _ = add_event(ready_service, age_s=age)
    mutate(ready_service, monkeypatch)
    called = []
    worker(ready_service, lambda *a: called.append(a) or "sent").step()
    row = outbox(ready_service, event_id)
    assert (row["state"], row["reason"]) == (state, reason) and not called


def test_warning_event_enqueues_push_in_same_transaction(service, monkeypatch):
    monkeypatch.setattr(
        service.policy,
        "ingest",
        lambda *_: [
            {
                "visit_id": "push-visit",
                "reason": "unresolved_presence",
                "severity": "warning",
                "audible": False,
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
    for _ in range(200):
        with service.store.connection() as db:
            state = db.execute(
                "SELECT state FROM receipts WHERE frame_id=?", (result["frame_id"],)
            ).fetchone()[0]
        if state not in {"accepted", "processing"}:
            break
        time.sleep(0.01)
    assert state == "processed"
    with service.store.connection() as db:
        rows = db.execute(
            "SELECT p.state FROM push_outbox p JOIN outbox o ON o.event_id=p.event_id"
        ).fetchall()
    assert [r[0] for r in rows] == ["pending"]


@pytest.mark.parametrize(
    "status,expected",
    [
        (201, "sent"),
        (410, "gone"),
        (404, "gone"),
        (429, "retry"),
        (503, "retry"),
        (400, "rejected"),
    ],
)
def test_send_web_push_encrypts_for_subscriber_and_maps_status(
    tmp_path, monkeypatch, status, expected
):
    import requests
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from security_app.push import send_web_push

    receiver = ec.generate_private_key(ec.SECP256R1())
    p256dh = b64url(
        receiver.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    generate_vapid(tmp_path)
    seen = {}

    def fake_post(self, url, data=None, headers=None, timeout=None, **kwargs):
        seen.update(url=url, data=data, headers=headers, timeout=timeout, proxies=self.trust_env)
        response = requests.Response()
        response.status_code = status
        return response

    monkeypatch.setattr(requests.Session, "post", fake_post)
    info = {"endpoint": ENDPOINT, "keys": {"p256dh": p256dh, "auth": AUTH}}
    outcome = send_web_push(
        info, {"title": "t"}, tmp_path / "vapid_private.pem", "mailto:a@b.org", 300
    )
    assert outcome == expected
    assert seen["url"] == ENDPOINT and seen["timeout"] == 5 and seen["proxies"] is False
    assert seen["headers"]["ttl"] == "300" and seen["headers"]["authorization"].startswith("vapid")
    assert b"title" not in seen["data"]
