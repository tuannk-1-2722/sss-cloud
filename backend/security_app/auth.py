from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Request

from .service import DomainError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, service):
        self.service = service
        path: Path = service.runtime / "credentials.json"
        self.credentials = json.loads(path.read_text()) if path.exists() else None
        self.failed_logins: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
        self.rate_lock = threading.Lock()
        self.hello_calls: deque = deque(maxlen=6)

    def login(self, username: str, password: str, ip: str) -> tuple[str, str]:
        with self.rate_lock:
            return self._login(username, password, ip)

    def _login(self, username: str, password: str, ip: str) -> tuple[str, str]:
        now = time.time()
        if ip not in self.failed_logins and len(self.failed_logins) >= 1024:
            self.failed_logins.pop(next(iter(self.failed_logins)))
        attempts = self.failed_logins[ip]
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        if len(attempts) >= 5:
            raise DomainError("LOGIN_RATE_LIMIT", 429)
        valid = False
        if self.credentials and secrets.compare_digest(
            username.encode(), self.credentials["username"].encode()
        ):
            try:
                valid = PasswordHasher().verify(self.credentials["password_hash"], password)
            except VerifyMismatchError:
                pass
        if not valid:
            attempts.append(now)
            raise DomainError("INVALID_CREDENTIALS", 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.service.store.connection() as db:
            db.execute(
                "INSERT INTO web_sessions VALUES(?,?,?)", (digest(token), digest(csrf), now + 28800)
            )
        attempts.clear()
        return token, csrf

    def admin(self, request: Request):
        token = request.cookies.get("sss_session", "")
        row = self.session(digest(token))
        if row is None:
            raise DomainError("LOGIN_REQUIRED", 401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(
                digest(request.headers.get("x-csrf-token", "")), row["csrf_hash"]
            ):
                raise DomainError("CSRF_INVALID", 403)
        return row

    def session(self, token_hash: str):
        with self.service.store.connection() as db:
            row = db.execute(
                "SELECT * FROM web_sessions WHERE token_hash=?", (token_hash,)
            ).fetchone()
        if not row or row["expires_at"] <= time.time():
            return None
        return row

    def device(self, request: Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise DomainError("DEVICE_TOKEN_INVALID", 401)
        token = header.removeprefix("Bearer ")
        expected = self.credentials.get("device_token_hash", "") if self.credentials else ""
        if not expected or not secrets.compare_digest(digest(token), expected):
            raise DomainError("DEVICE_TOKEN_INVALID", 401)
        if request.path_params.get("device_id") != self.service.cfg["device_id"]:
            raise DomainError("DEVICE_FORBIDDEN", 403)

    def hello_rate_limit(self):
        with self.rate_lock:
            self._hello_rate_limit()

    def _hello_rate_limit(self):
        now = time.time()
        while self.hello_calls and now - self.hello_calls[0] > 60:
            self.hello_calls.popleft()
        if len(self.hello_calls) >= 6:
            raise DomainError("HELLO_RATE_LIMIT", 429)
        self.hello_calls.append(now)
