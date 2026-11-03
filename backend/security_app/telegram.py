"""Telegram formatting and transport; never owns policy, commands or credentials storage."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx


class _TokenRedaction(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(
            r"https://api\.telegram\.org/bot[^/\s]+",
            "https://api.telegram.org/bot[REDACTED]",
            record.getMessage(),
        )
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_TokenRedaction())


def dashboard_link() -> str | None:
    url = os.environ.get("SSS_DASHBOARD_URL", "").strip()
    if not url or len(url) > 512:
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    if parsed.fragment or parsed.query or any(c.isspace() for c in url):
        return None
    return url.rstrip("/") + "/"


def configuration(cfg):
    token, chat = os.environ.get("SSS_TELEGRAM_TOKEN", ""), os.environ.get("SSS_TELEGRAM_CHAT", "")
    reason = None
    if not cfg["telegram_enabled"]:
        reason = "DISABLED"
    elif not token or not chat:
        reason = "PROVIDER_NOT_CONFIGURED"
    elif len(token) > 512 or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        reason = "BOT_TOKEN_INVALID"
    elif not re.fullmatch(r"-?[1-9][0-9]{0,19}", chat):
        reason = "CHAT_ID_INVALID"
    photos = cfg["telegram_include_photos"] and os.environ.get("SSS_TELEGRAM_PHOTO_CONSENT") == "1"
    return {
        "enabled": cfg["telegram_enabled"],
        "ready": reason is None,
        "reason": reason,
        "photos_enabled": photos,
        "photo_consent_required": cfg["telegram_include_photos"] and not photos,
        "dashboard_link_ready": dashboard_link() is not None,
        # Retain response keys for old dashboards; legacy YAML flags cannot opt in.
        "known_visits_enabled": False,
        "unresolved_visits_enabled": False,
    }


def telegram_message(row) -> tuple[str, dict | None]:
    reason = {
        "unknown_approach": "CẢNH BÁO: khách chưa đăng ký tiếp cận cửa",
        "unresolved_presence": "CẦN KIỂM TRA: chưa đủ căn cứ nhận diện",
        "capacity_exceeded": "CẦN KIỂM TRA: quá nhiều người trong vùng cửa",
        "known_visit": "Người đã đăng ký ghé cửa",
        "visit_completed": "LƯỢT ĐÃ KẾT THÚC: chưa đủ căn cứ xác nhận danh tính",
        "notification_test": "THỬ THÔNG BÁO — không phải phát hiện AI hoặc lệnh còi",
    }.get(row["event_reason"], "Sự kiện camera vùng cửa")
    local_time = datetime.fromisoformat(row["created_at"]).astimezone(timezone(timedelta(hours=7)))
    text = (
        f"Smart Security | {reason}\nCamera: cửa chính\n"
        f"Thời điểm: {local_time:%d/%m/%Y %H:%M:%S} (UTC+7)\nSự kiện: {row['event_id']}"
    )
    link = dashboard_link()
    keyboard = (
        {"inline_keyboard": [[{"text": "Xem camera trực tiếp / lịch sử", "url": link + "?live=1"}]]}
        if link
        else None
    )
    return text, keyboard


@dataclass(frozen=True)
class Delivery:
    state: str
    reason: str | None = None
    retry_after: int | None = None
    message_id: int | None = None
    media_kind: str = "text"


def send(row, photo: bytes | None) -> Delivery:
    """All errors are finite codes. Never expose provider URL/exception text (bot token)."""
    text, keyboard = telegram_message(row)
    token, chat = os.environ["SSS_TELEGRAM_TOKEN"], os.environ["SSS_TELEGRAM_CHAT"]
    common = {"chat_id": chat, "protect_content": True}
    timeout = httpx.Timeout(5, connect=3, write=5, pool=2)
    mode = "photo" if photo else "text"
    try:
        if photo:
            response = httpx.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
                data={
                    **common,
                    "caption": text,
                    **({"reply_markup": json.dumps(keyboard)} if keyboard else {}),
                },
                files={"photo": ("evidence.jpg", photo, "image/jpeg")},
            )
        else:
            response = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
                json={**common, "text": text, **({"reply_markup": keyboard} if keyboard else {})},
            )
        payload = response.json()
        if not isinstance(payload, dict):
            return Delivery("retry", "DELIVERY_UNCONFIRMED", media_kind=mode)
        if response.is_success and payload.get("ok") is True:
            result = payload.get("result")
            mid = result.get("message_id") if isinstance(result, dict) else None
            if type(mid) is not int or mid <= 0:
                return Delivery("unknown", "PROVIDER_RESPONSE_INVALID", media_kind=mode)
            return Delivery("sent", message_id=mid, media_kind=mode)
        if response.status_code == 429 or payload.get("error_code") == 429:
            parameters = payload.get("parameters")
            delay = parameters.get("retry_after", 2) if isinstance(parameters, dict) else 2
            delay = delay if type(delay) is int and delay > 0 else 2
            return Delivery("retry", "RATE_LIMIT", retry_after=delay, media_kind=mode)
        if response.status_code >= 500:
            return Delivery("retry", "PROVIDER_ERROR", media_kind=mode)
        if response.status_code == 401:
            return Delivery("failed", "BOT_TOKEN_REJECTED", media_kind=mode)
        if response.status_code == 403:
            return Delivery("failed", "BOT_BLOCKED_OR_FORBIDDEN", media_kind=mode)
        if photo and response.status_code == 400:
            description = str(payload.get("description", "")).lower()
            if any(word in description for word in ("photo", "image", "file", "dimension")):
                fallback = send(row, None)  # A definitive rejected photo was not delivered.
                return Delivery(
                    fallback.state,
                    fallback.reason or "PHOTO_REJECTED_TEXT_SENT",
                    fallback.retry_after,
                    fallback.message_id,
                    fallback.media_kind,
                )
        return Delivery("failed", "PROVIDER_REJECTED", media_kind=mode)
    except (httpx.HTTPError, ValueError, TypeError):
        return Delivery("retry", "DELIVERY_UNCONFIRMED", media_kind=mode)
