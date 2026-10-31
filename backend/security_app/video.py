"""Bounded video fanout and renewable viewer demand, independent of model work."""

from __future__ import annotations

import asyncio
import struct
from collections import deque
from uuid import uuid4

from pydantic import ValidationError

from .contracts import VideoMeta
from .runtime import DomainError

VIDEO_HEADER_BYTES = 2048
VIDEO_MESSAGE_BYTES = 2 + VIDEO_HEADER_BYTES + 524288


def unpack_video(data: bytes) -> tuple[VideoMeta, bytes]:
    if len(data) < 3 or len(data) > VIDEO_MESSAGE_BYTES:
        raise DomainError("VIDEO_MESSAGE_SIZE", 413)
    length = struct.unpack_from("<H", data)[0]
    if not 1 <= length <= VIDEO_HEADER_BYTES or len(data) <= 2 + length:
        raise DomainError("VIDEO_HEADER_SIZE", 422)
    try:
        meta = VideoMeta.model_validate_json(data[2 : 2 + length])
    except ValidationError as exc:
        raise DomainError("INVALID_VIDEO_METADATA", 422) from exc
    return meta, data[2 + length :]


def pack_video(meta: VideoMeta, jpeg: bytes) -> bytes:
    header = meta.model_dump_json().encode()
    if len(header) > VIDEO_HEADER_BYTES or not 1 <= len(jpeg) <= 524288:
        raise ValueError("Video message too large")
    return struct.pack("<H", len(header)) + header + jpeg


class VideoHub:
    """All mutations use the service lock; subscribers retain no frame queue."""

    def __init__(self, service):
        self.service = service
        self.latest = None
        self.revision = 1
        self.views: dict[str, dict] = {}
        self.listeners: dict[asyncio.Event, asyncio.AbstractEventLoop] = {}
        self.received = deque(maxlen=80)
        self.last_seq = -1
        self.last_capture_ms = -1
        self.enrollment_demand = False

    def notify(self):
        for event, loop in tuple(self.listeners.items()):
            if not loop.is_closed():
                loop.call_soon_threadsafe(event.set)

    def reset(self):
        self.latest = None
        self.revision += 1
        self.last_seq = self.last_capture_ms = -1
        self.received.clear()
        self.notify()

    def expire(self):
        now = self.service.now()
        previous = len(self.views)
        self.views = {key: view for key, view in self.views.items() if view["deadline"] > now}
        self._sampling_changed()
        if len(self.views) != previous:
            self.notify()

    def _sampling_changed(self):
        s = self.service
        capture = s.capture_session
        demand = bool(
            capture
            and any(v["enrollment_session_id"] == capture["id"] for v in self.views.values())
        )
        if demand != self.enrollment_demand:
            self.enrollment_demand = demand
            # A resume creates a fresh camera barrier, including when inference is in flight.
            if capture:
                s.mode_revision += 1
                s._cancel_pending()
                s._issue("STOP_ALARM")
                s.condition.notify_all()
            self.notify()

    def renew(self, owner: str, lease_id: str | None, enrollment_id: str | None):
        s = self.service
        with s.condition:
            self.expire()
            if enrollment_id and (
                not s.capture_session or s.capture_session["id"] != enrollment_id
            ):
                raise DomainError("ENROLLMENT_NOT_ACTIVE")
            if lease_id:
                view = self.views.get(lease_id)
                if not view or view["owner"] != owner:
                    raise DomainError("VIDEO_VIEW_EXPIRED", 404)
            else:
                if len(self.views) >= s.cfg["video"]["max_viewers"]:
                    raise DomainError("LIVE_VIEW_LIMIT", 429)
                lease_id = str(uuid4())
            self.views[lease_id] = {
                "owner": owner,
                "enrollment_session_id": enrollment_id,
                "deadline": s.now() + s.cfg["video"]["viewer_lease_ms"],
            }
            self._sampling_changed()
            self.notify()
            return {"lease_id": lease_id, "remaining_ms": s.cfg["video"]["viewer_lease_ms"]}

    def release(self, owner: str, lease_id: str | None = None):
        with self.service.condition:
            self.views = {
                key: value
                for key, value in self.views.items()
                if not (value["owner"] == owner and (lease_id is None or key == lease_id))
            }
            self._sampling_changed()
            self.notify()

    def authorized(self, owner: str, lease_id: str) -> bool:
        self.expire()
        view = self.views.get(lease_id)
        return bool(view and view["owner"] == owner)

    def sampling(self) -> bool:
        self.expire()
        capture = self.service.capture_session
        return bool(
            capture
            and self.enrollment_demand
            and capture["accepted"][capture["round"] - 1]
            < self.service.cfg["enrollment"]["per_round_min"]
        )

    def demand(self):
        self.expire()
        s = self.service
        watching = bool(self.views)
        enabled = watching or s.mode == "ARMED"
        return {
            "enabled": enabled,
            "interval_ms": s.cfg["video"]["view_interval_ms" if watching else "armed_interval_ms"],
            "lease_ms": s.cfg["alarm"]["control_lost_ms"] if enabled else 0,
            "revision": self.revision,
            "protocol_version": 1,
            "window_frames": 2,
        }

    def publish(self, frame_id, jpeg, received_ms, mode_revision, capture_age_ms=0):
        self.latest = {
            "frame_id": frame_id,
            "jpeg": jpeg,
            "received_ms": received_ms,
            "mode_revision": mode_revision,
            "video_revision": self.revision,
            "capture_age_ms": capture_age_ms,
        }
        self.received.append(received_ms)
        self.notify()

    def metrics(self):
        span = self.received[-1] - self.received[0] if len(self.received) > 1 else 0
        fps = (len(self.received) - 1) * 1000 / span if span > 0 else 0
        if self.received and self.service.now() - self.received[-1] > 3000:
            fps = 0
        return {
            "received_fps": round(fps, 2),
            "viewers": len(self.views),
            "revision": self.revision,
            "age_basis": "sync_clock_estimate"
            if self.service.device_clock_offsets
            else "received_plus_device_age",
        }
