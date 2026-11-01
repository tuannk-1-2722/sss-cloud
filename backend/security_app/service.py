from __future__ import annotations

import hashlib
import io
import json
import logging
import sqlite3
import threading
from collections import deque
from functools import wraps
from pathlib import Path
from typing import Callable
from uuid import uuid4

from filelock import FileLock
from PIL import Image

from .config import calibration_status, ensure_runtime_profile
from .contracts import FrameMeta, Hello, Observation, Sync, VideoMeta
from .policy import PolicyEngine
from .ports import VisionPort
from .runtime import DomainError as DomainError
from .runtime import Packet as Packet
from .runtime import clock_ms as clock_ms
from .storage import Store, utcnow

logger = logging.getLogger(__name__)


def storage_guard(method):
    """Fail closed under the same coordinator lock, before another sync can run."""

    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.lock:
            try:
                return method(self, *args, **kwargs)
            except (sqlite3.Error, OSError) as exc:
                self.fail_closed("STORAGE_FAULT")
                raise DomainError("STORAGE_FAULT", 503) from exc

    return guarded


class SecurityService:
    def __init__(self, config: dict, *, vision_factory: Callable[[dict], VisionPort] | None = None):
        self.cfg = config
        self.vision_factory = vision_factory
        self.runtime = Path(config["runtime_dir"])
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.instance_lock = FileLock(str(self.runtime / "server.lock"))
        self.instance_lock.acquire(timeout=0)
        try:
            ensure_runtime_profile(self.runtime, config["profile"])
        except Exception:
            self.instance_lock.release()
            raise
        try:
            self.store = Store(self.runtime)
            self.calibration, self.calibration_error = calibration_status(config)
        except Exception:
            self.instance_lock.release()
            raise
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.stopping = False
        self.mode, self.mode_revision, self.gallery_revision = "DISARMED", 1, 1
        self.policy = PolicyEngine(config)
        self.session_id = None
        self.boot_id = None
        self.source = "simulator" if config["profile"] == "simulation" else "hardware"
        self.last_device_ms = 0
        self.last_analysis_ms = 0
        self.last_frame_ms = 0
        self.last_presence_ms = 0
        self.last_frame_seq = -1
        self.last_sync_seq = -1
        self.device_clock_offsets = deque(maxlen=60)
        self.sync_cache = None
        self.pending: Packet | None = None
        self.processing = False
        self.latest_analysis: dict | None = None
        self.environment_quality: dict | None = None
        self.generation = 0
        self.command: dict | None = None
        self.command_deadline = 0
        self.buzzer_reported = "UNKNOWN"
        self.device_telemetry = None
        self.last_stage_ms = None
        self.capture_session: dict | None = None
        self.vision_ready = config["profile"] == "simulation"
        self.vision_error = None
        self.worker_error = None
        self.dropped = 0
        self.upload_tokens = float(config["limits"]["upload_burst"])
        self.upload_token_time = clock_ms()
        from .enrollment import EnrollmentManager

        self.enrollment = EnrollmentManager(self)
        self.notification_error = None
        self.cloud_error = None
        self.sync_fingerprint = None
        from .video import VideoHub

        self.video = VideoHub(self)
        self.analysis_times = deque(maxlen=128)
        self.analysis_durations = deque(maxlen=128)
        self.last_ai_start_ms = 0
        self.last_ai_queue_ms = 0
        self.active_ids_revision, self.active_ids = -1, set()
        from .worker import VisionWorker

        self.worker = VisionWorker(self)
        self.thread = threading.Thread(target=self.worker.run, name="vision-worker", daemon=True)
        self.thread.start()

    def now(self):
        return clock_ms()

    @property
    def latest(self):
        # Compatibility for the recorder; this is now raw video, never model output.
        return self.video.latest

    @latest.setter
    def latest(self, value):
        self.video.latest = value

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
            self.video.notify()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            # A stuck native inference requires process exit; do not allow a second DB owner.
            logger.error("Worker did not stop; runtime lock retained until process exit")
        else:
            self.instance_lock.release()

    @storage_guard
    def hello(self, request: Hello) -> dict:
        with self.lock:
            if request.source != self.source:
                raise DomainError("SOURCE_PROFILE_MISMATCH", 403)
            if request.source == "hardware" and request.psram_bytes <= 0:
                raise DomainError("PSRAM_REQUIRED", 422)
            if request.boot_id != self.boot_id or self.session_id is None:
                self._disarm("device_boot")
                with self.store.connection() as db:
                    db.execute("UPDATE commands SET state='cancelled' WHERE state='pending'")
                self.boot_id, self.session_id = request.boot_id, str(uuid4())
                self.last_frame_seq, self.last_sync_seq = -1, -1
                self.generation, self.command = 0, None
                self.sync_cache = None
                self.sync_fingerprint = None
                self.device_clock_offsets.clear()
                self.video.reset()
            self.last_device_ms = clock_ms()
            return {
                "protocol_version": 2,
                "session_id": self.session_id,
                "mode": self.mode,
                "mode_revision": self.mode_revision,
                "max_frame_bytes": self.cfg["limits"]["max_frame_bytes"],
                "video_protocol_version": 1,
            }

    def check_session(self, session_id: str, boot_id: str):
        if session_id != self.session_id or boot_id != self.boot_id or self.session_id is None:
            raise DomainError("SESSION_INVALID")

    @storage_guard
    def sync(self, request: Sync) -> dict:
        with self.lock:
            self.check_session(request.session_id, request.boot_id)
            self._expire_capture()
            fingerprint = request.model_dump_json()
            if request.sync_seq == self.last_sync_seq and self.sync_cache is not None:
                if fingerprint != self.sync_fingerprint:
                    raise DomainError("SYNC_ID_REUSED")
                # Cached responses cannot revive leases: recompute remaining TTL each retry.
                return self._sync_response(request.sync_seq, self.sync_cache["acked_command_ids"])
            if request.sync_seq < self.last_sync_seq:
                raise DomainError("SYNC_SEQUENCE_STALE")
            self.last_device_ms = clock_ms()
            self.buzzer_reported = request.buzzer_state
            self.device_clock_offsets.append(self.last_device_ms - request.uptime_ms)
            self.device_telemetry = request.telemetry.model_dump() if request.telemetry else None
            acked = []
            with self.store.connection() as db:
                for ack in request.acks:
                    row = db.execute(
                        "SELECT * FROM commands WHERE id=?", (ack.command_id,)
                    ).fetchone()
                    if row and row["generation"] == ack.generation:
                        original = json.loads(row["payload_json"])
                        if original["session_id"] != self.session_id:
                            continue
                        # A late ACK never changes superseded command state or current generation.
                        if row["state"] == "pending":
                            db.execute(
                                "UPDATE commands SET state=?,ack_json=? WHERE id=?",
                                (
                                    "rejected" if ack.status == "rejected" else "acked",
                                    ack.model_dump_json(),
                                    ack.command_id,
                                ),
                            )
                        acked.append(ack.command_id)
                        if self.command and self.command["command_id"] == ack.command_id:
                            self.command = None
            self.sync_fingerprint = fingerprint
            self.last_sync_seq = request.sync_seq
            self.sync_cache = self._sync_response(request.sync_seq, acked)
            return self.sync_cache

    def _sync_response(self, sync_seq: int, acked: list[str]) -> dict:
        now = clock_ms()
        self._check_health(now)
        self._expire_command(now)
        video = self.video.demand()
        command = (
            {**self.command, "remaining_ttl_ms": self.command_deadline - now}
            if self.command
            else None
        )
        capture = self.capture_session
        return {
            "sync_seq": sync_seq,
            "session_id": self.session_id,
            "mode": self.mode,
            "mode_revision": self.mode_revision,
            "generation": self.generation,
            "command": command,
            "acked_command_ids": acked,
            "capture_purpose": capture["purpose"] if capture else None,
            "capture_session_id": capture["id"] if capture else None,
            "capture_lease_ms": max(0, capture["deadline"] - now) if capture else 0,
            "capture_hold_ms": self.cfg["camera"]["presence_hold_ms"]
            if self.mode == "ARMED"
            and now - self.last_presence_ms < self.cfg["camera"]["presence_hold_ms"]
            and self.worker_error is None
            else 0,
            "camera": self.cfg["camera"],
            "video": video,
        }

    def _expire_command(self, now):
        if self.command and self.command_deadline <= now:
            with self.store.connection() as db:
                db.execute(
                    "UPDATE commands SET state='expired' WHERE id=? AND state='pending'",
                    (self.command["command_id"],),
                )
            self.command = None

    def _issue(self, kind: str, visit_id: str | None = None, db=None) -> dict | None:
        if self.session_id is None:
            return None
        self.generation += 1
        duration = (
            self.cfg["alarm"]["test_cap_ms"]
            if kind == "TEST_ALARM"
            else self.cfg["alarm"]["duration_ms"]
            if kind == "START_ALARM"
            else 0
        )
        command = {
            "command_id": str(uuid4()),
            "type": kind,
            "generation": self.generation,
            "session_id": self.session_id,
            "boot_id": self.boot_id,
            "mode_revision": self.mode_revision,
            "duration_ms": duration,
            "visit_id": visit_id,
        }

        def persist(connection):
            connection.execute("UPDATE commands SET state='superseded' WHERE state='pending'")
            connection.execute(
                "INSERT INTO commands VALUES(?,?,?,'pending',?,NULL)",
                (command["command_id"], self.generation, json.dumps(command), utcnow()),
            )

        if db is None:
            with self.store.connection() as connection:
                persist(connection)
        else:
            persist(db)
        if db is None:
            self._deliver_committed(command)
        return command

    def _deliver_committed(self, command):
        # Only call after the surrounding transaction exits successfully.
        self.command = command
        self.command_deadline = clock_ms() + self.cfg["alarm"]["ttl_ms"]

    def fail_closed(self, reason):
        # No I/O before all actuator-capable state is invalidated.
        self.mode, self.mode_revision = "DISARMED", self.mode_revision + 1
        self.command = None
        pending, self.pending = self.pending, None
        self.capture_session = None
        self.latest_analysis = None
        self.environment_quality = None
        self.last_presence_ms = 0
        self.policy.reset()
        self.worker_error = reason
        self.video._sampling_changed()
        try:
            with self.store.connection() as db:
                db.execute("DELETE FROM samples WHERE committed=0")
                db.execute(
                    "UPDATE receipts SET state='cancelled',reason=? WHERE state='accepted'",
                    (reason,),
                )
            self._issue("STOP_ALARM")
        except (sqlite3.Error, OSError):
            self.command = None
        return pending

    def _cancel_pending(self):
        pending, self.pending = self.pending, None
        if pending:
            self.receipt(pending, "cancelled", "MODE_CHANGED")
        self.condition.notify_all()

    def _disarm(self, reason: str):
        self.mode, self.mode_revision = "DISARMED", self.mode_revision + 1
        self.command = None
        self.policy.reset()
        self._cancel_pending()
        if self.capture_session and self.capture_session["purpose"] == "enrollment":
            with self.store.connection() as db:
                db.execute(
                    "DELETE FROM samples WHERE session_id=? AND committed=0",
                    (self.capture_session["id"],),
                )
        self.capture_session = None
        self.latest_analysis = None
        self.environment_quality = None
        self.last_presence_ms = 0
        self.video._sampling_changed()
        self._issue("STOP_ALARM")
        self.store.audit(reason)

    @storage_guard
    def set_mode(self, mode: str, expected_revision: int) -> dict:
        with self.lock:
            if expected_revision != self.mode_revision:
                raise DomainError("MODE_REVISION_CONFLICT")
            if mode == "DISARMED":
                self._disarm("admin_disarm")
            elif self.mode != "ARMED":
                if self.capture_session:
                    raise DomainError("CAPTURE_SESSION_ACTIVE")
                if not self.vision_ready:
                    raise DomainError(self.vision_error or "VISION_NOT_READY", 503)
                if self.calibration_error:
                    raise DomainError(self.calibration_error)
                try:
                    gallery = self.store.gallery()
                except ValueError as exc:
                    raise DomainError("GALLERY_REENROLL_REQUIRED") from exc
                if not gallery:
                    raise DomainError("EMPTY_GALLERY")
                limit = (
                    self.cfg["enrollment"]["max_people"]
                    if self.cfg["profile"] == "simulation"
                    else min(
                        self.cfg["enrollment"]["max_people"],
                        self.calibration.get("validated_gallery_limit", 0),
                    )
                )
                if len(gallery) > limit:
                    raise DomainError("CALIBRATED_CAPACITY_REACHED")
                if any(
                    len(vectors) < self.cfg["enrollment"]["gallery_min"]
                    for vectors in gallery.values()
                ):
                    raise DomainError("GALLERY_SAMPLES_REQUIRED")
                if (
                    self.session_id is None
                    or clock_ms() - self.last_device_ms > self.cfg["limits"]["device_offline_ms"]
                ):
                    raise DomainError("DEVICE_OFFLINE")
                self.mode, self.mode_revision = "ARMED", self.mode_revision + 1
                self.policy.reset()
                self._cancel_pending()
                self.latest_analysis = None
                self.last_presence_ms = 0
                self.last_analysis_ms = clock_ms()
                self.store.audit("admin_arm")
            return {"mode": self.mode, "mode_revision": self.mode_revision}

    @storage_guard
    def action(self, key: str, route: str, visit_id: str | None = None) -> dict:
        with self.lock:
            fingerprint = json.dumps([route, visit_id])
            with self.store.connection() as db:
                row = db.execute("SELECT * FROM action_keys WHERE key=?", (key,)).fetchone()
            previous = (
                {"fingerprint": row["fingerprint"], "response": json.loads(row["response_json"])}
                if row
                else None
            )
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise DomainError("IDEMPOTENCY_KEY_REUSED")
                return previous["response"]
            if (
                self.session_id is None
                or clock_ms() - self.last_device_ms > self.cfg["limits"]["device_offline_ms"]
            ):
                raise DomainError("DEVICE_OFFLINE")
            if route == "test":
                if self.mode != "DISARMED" or self.capture_session:
                    raise DomainError("TEST_REQUIRES_DISARMED")
                kind = "TEST_ALARM"
            elif route == "silence":
                if not any(t.visit_id == visit_id for t in self.policy.tracks.values()):
                    raise DomainError("VISIT_NOT_ACTIVE", 404)
                self.policy.silence(visit_id)
                kind = "STOP_ALARM"
            else:
                raise DomainError("INVALID_ACTION", 422)
            try:
                with self.store.connection() as db:
                    command = self._issue(kind, db=db)
                    response = {"command_id": command["command_id"], "state": "pending"}
                    db.execute(
                        "INSERT INTO action_keys VALUES(?,?,?,?)",
                        (key, fingerprint, json.dumps(response), utcnow()),
                    )
            except Exception:
                self.command = None
                self.mode, self.mode_revision = "DISARMED", self.mode_revision + 1
                raise
            self._deliver_committed(command)
            return response

    @storage_guard
    def start_capture(self, purpose: str, person_id: str | None = None) -> dict:
        return self.enrollment.start_capture(purpose, person_id)

    def capture_status(self) -> dict:
        return self.enrollment.capture_status()

    def _expire_capture(self):
        return self.enrollment._expire_capture()

    @storage_guard
    def next_enrollment_round(self, session_id: str) -> dict:
        return self.enrollment.next_enrollment_round(session_id)

    def accept(
        self,
        meta: FrameMeta,
        jpeg: bytes | None = None,
        observations: list[Observation] | None = None,
    ) -> dict:
        if jpeg is not None:
            self.validate_jpeg(jpeg)
        with self.condition:
            self.check_session(meta.session_id, meta.boot_id)
            if observations is not None and self.cfg["profile"] != "simulation":
                raise DomainError("SYNTHETIC_INPUT_DISABLED", 403)
            body = (
                jpeg
                if jpeg is not None
                else json.dumps(
                    [o.model_dump() for o in observations or []], sort_keys=True
                ).encode()
            )
            fingerprint = hashlib.sha256(meta.model_dump_json().encode() + body).hexdigest()
            with self.store.connection() as db:
                previous = db.execute(
                    "SELECT * FROM receipts WHERE boot_id=? AND seq=?", (meta.boot_id, meta.seq)
                ).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise DomainError("FRAME_ID_REUSED")
                    return {
                        "frame_id": previous["frame_id"],
                        "state": previous["state"],
                        "duplicate": True,
                    }
            if meta.seq <= self.last_frame_seq:
                raise DomainError("STALE_FRAME_SEQUENCE")
            self._expire_capture()
            if meta.mode_revision != self.mode_revision:
                raise DomainError("MODE_REVISION_CONFLICT")
            if meta.capture_age_ms > self.cfg["limits"]["max_capture_age_ms"]:
                raise DomainError("CAPTURE_TOO_OLD", 422)
            if meta.purpose == "monitoring":
                if self.mode != "ARMED":
                    raise DomainError("MONITORING_NOT_ARMED")
            elif (
                not self.capture_session
                or meta.capture_session_id != self.capture_session["id"]
                or meta.purpose != self.capture_session["purpose"]
            ):
                raise DomainError("CAPTURE_LEASE_INVALID")
            now = clock_ms()
            self._upload_token(now)
            frame_id = str(uuid4())
            with self.store.connection() as db:
                db.execute(
                    "INSERT INTO receipts VALUES(?,?,?,?, 'accepted',?,NULL)",
                    (frame_id, meta.boot_id, meta.seq, fingerprint, utcnow()),
                )
            self.last_frame_seq = meta.seq
            if jpeg:
                self.video.publish(frame_id, jpeg, now, meta.mode_revision, meta.capture_age_ms)
                self.last_frame_ms = now
            self._queue(Packet(frame_id, meta, now, self.gallery_revision, jpeg, observations))
            return {"frame_id": frame_id, "state": "accepted", "duplicate": False}

    def validate_jpeg(self, jpeg: bytes):
        if len(jpeg) > self.cfg["limits"]["max_frame_bytes"]:
            raise DomainError("FRAME_TOO_LARGE", 413)
        try:
            with Image.open(io.BytesIO(jpeg)) as image:
                w, h = image.size
                if image.format != "JPEG":
                    raise DomainError("JPEG_REQUIRED", 415)
                if not (
                    0 < w <= self.cfg["limits"]["max_decode_width"]
                    and 0 < h <= self.cfg["limits"]["max_decode_height"]
                ):
                    raise DomainError("DECODE_DIMENSIONS_TOO_LARGE", 422)
                if self.cfg["profile"] == "hardware" and (w, h) != (
                    self.cfg["camera"]["width"],
                    self.cfg["camera"]["height"],
                ):
                    raise DomainError("CAMERA_PROFILE_DIMENSIONS_MISMATCH", 422)
                image.load()  # Decode bounded dimensions fully; JPEG verify() alone is insufficient.
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError("INVALID_JPEG", 422) from exc

    def _upload_token(self, now):
        self.upload_tokens = min(
            float(self.cfg["limits"]["upload_burst"]),
            self.upload_tokens
            + (now - self.upload_token_time) / 1000 * self.cfg["limits"]["max_uploads_per_second"],
        )
        self.upload_token_time = now
        if self.upload_tokens < 1:
            raise DomainError("UPLOAD_RATE_LIMIT", 429)
        self.upload_tokens -= 1

    def _queue(self, packet):
        if self.pending:
            self.receipt(self.pending, "replaced")
            self.dropped += 1
        self.pending = packet
        self.condition.notify_all()

    def accept_video(self, meta: VideoMeta, jpeg: bytes, received_ms: int | None = None) -> dict:
        """No per-video-frame SQLite writes. Video is published before AI is scheduled."""
        received = self.now() if received_ms is None else received_ms
        self.validate_jpeg(jpeg)
        with self.condition:
            self.check_session(meta.session_id, meta.boot_id)
            if meta.seq <= self.video.last_seq:
                raise DomainError("STALE_FRAME_SEQUENCE")
            if meta.captured_uptime_ms <= self.video.last_capture_ms:
                raise DomainError("STALE_CAPTURE_TIMESTAMP")
            self._expire_capture()
            demand = self.video.demand()
            if not demand["enabled"]:
                raise DomainError("VIDEO_NOT_REQUESTED")
            if self.now() - self.last_device_ms >= self.cfg["alarm"]["control_lost_ms"]:
                raise DomainError("CONTROL_EXPIRED")
            if meta.mode_revision > self.mode_revision:
                raise DomainError("MODE_REVISION_CONFLICT")
            observed = received - meta.capture_age_ms
            if self.device_clock_offsets:
                # Upper bound on device/server clock offset from sync request arrivals.
                # Choosing the earliest estimate includes measurable WAN/queue delay.
                observed = min(observed, meta.captured_uptime_ms + min(self.device_clock_offsets))
            capture_age = max(0, received - observed)
            if self.now() - observed > 1000:
                raise DomainError("CAPTURE_TOO_OLD", 422)
            self._upload_token(self.now())
            frame_id = str(uuid4())
            self.video.last_seq, self.video.last_capture_ms = meta.seq, meta.captured_uptime_ms
            self.video.publish(frame_id, jpeg, received, meta.mode_revision, capture_age)
            self.last_frame_ms = received
            purpose = "monitoring" if self.mode == "ARMED" else "enrollment"
            eligible = meta.mode_revision == self.mode_revision and (
                self.mode == "ARMED"
                or (
                    self.mode == "ENROLLMENT"
                    and self.video.sampling()
                    and meta.capture_session_id == self.capture_session["id"]
                )
            )
            # Simulation uses the explicitly tagged observation endpoint, never fake JPEG AI.
            eligible = eligible and (
                self.cfg["profile"] == "hardware" or self.vision_factory is not None
            )
            if eligible:
                ai_meta = FrameMeta(
                    session_id=meta.session_id,
                    boot_id=meta.boot_id,
                    seq=meta.seq,
                    mode_revision=meta.mode_revision,
                    purpose=purpose,
                    capture_age_ms=meta.capture_age_ms,
                    capture_session_id=meta.capture_session_id,
                )
                self._queue(
                    Packet(
                        frame_id,
                        ai_meta,
                        received,
                        self.gallery_revision,
                        jpeg,
                        durable_receipt=False,
                        observed_ms=observed,
                    )
                )
            return {
                "seq": meta.seq,
                "frame_id": frame_id,
                "state": "video_received",
                "ai_candidate": eligible,
            }

    def receipt(self, packet: Packet, state: str, reason: str | None = None):
        if packet.durable_receipt:
            self._receipt_state(packet.frame_id, state, reason)

    def _receipt_state(self, frame_id: str, state: str, reason: str | None = None):
        with self.store.connection() as db:
            db.execute(
                "UPDATE receipts SET state=?,reason=? WHERE frame_id=?", (state, reason, frame_id)
            )

    def _check_health(self, now: int):
        if (
            self.mode == "ARMED"
            and now - self.last_analysis_ms > self.cfg["limits"]["analysis_fault_ms"]
        ):
            if self.worker_error != "ANALYSIS_INTERRUPTED":
                self.policy.reset()
            self.worker_error = "ANALYSIS_INTERRUPTED"
            self.last_presence_ms = 0

    def status(self) -> dict:
        with self.lock:
            self._expire_capture()
            now = clock_ms()
            self._check_health(now)
            self._expire_command(now)
            people = self.store.people()
            return {
                "zones": self.cfg["zones"],
                "notification_error": self.notification_error,
                "cloud_error": self.cloud_error,
                "ui_limits": {
                    "test_cap_ms": self.cfg["alarm"]["test_cap_ms"],
                    "evidence_days": self.cfg["retention"]["evidence_days"],
                },
                "video": {**self.video.metrics(), **self.video.demand(), **self.cfg["video"]},
                "analysis": self.analysis_metrics(now),
                "device_telemetry": self.device_telemetry,
                "profile": self.cfg["profile"],
                "source": self.source,
                "mode": self.mode,
                "mode_revision": self.mode_revision,
                "gallery_revision": self.gallery_revision,
                "device_online": bool(self.session_id)
                and now - self.last_device_ms <= self.cfg["limits"]["device_offline_ms"],
                "frame_age_ms": now - self.last_frame_ms if self.last_frame_ms else None,
                "analysis_age_ms": now - self.last_analysis_ms if self.last_analysis_ms else None,
                "vision_ready": self.vision_ready,
                "vision_error": self.vision_error,
                "worker_error": self.worker_error,
                "calibration_error": self.calibration_error,
                "calibration_id": self.calibration.get("calibration_id"),
                "active_people": sum(p["active"] for p in people),
                "enrollment_requirements": {
                    k: self.cfg["enrollment"][k]
                    for k in ("per_round_min", "accepted_min", "max_people", "face_region")
                },
                "buzzer_reported": self.buzzer_reported,
                "pending_command": self.command,
                "processing": self.processing,
                "pending_frame": bool(self.pending),
                "frames_replaced": self.dropped,
                "tracks": self.policy.snapshot(),
                "capture": self.capture_status(),
                "latest_analysis": self.latest_analysis,
                "environment_quality": self.environment_quality,
                "notifications_enabled": self.cfg["notifications"]["telegram_enabled"],
            }

    @storage_guard
    def create_person(self, name: str) -> dict:
        return self.enrollment.create_person(name)

    @storage_guard
    def deactivate_person(self, person_id: str, delete: bool = False):
        return self.enrollment.deactivate_person(person_id, delete)

    def _enrollment_result(self, packet: Packet, vector, quality: dict):
        return self.enrollment._enrollment_result(packet, vector, quality)

    @storage_guard
    def commit_enrollment(self, session_id: str, expected_revision: int) -> dict:
        return self.enrollment.commit_enrollment(session_id, expected_revision)

    @storage_guard
    def activate_person(self, person_id: str):
        return self.enrollment.activate_person(person_id)

    @storage_guard
    def acknowledge(self, event_id: str):
        with self.lock, self.store.connection() as db:
            if not db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
                raise DomainError("EVENT_NOT_FOUND", 404)
            db.execute(
                "UPDATE events SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE id=?",
                (utcnow(), event_id),
            )

    def _publish(self, packet: Packet, observations: list[Observation]):
        now = clock_ms()
        if self.active_ids_revision != self.gallery_revision:
            self.active_ids = {p["id"] for p in self.store.people() if p["active"]}
            self.active_ids_revision = self.gallery_revision
        active_ids = self.active_ids
        if any(o.decision == "KNOWN" and o.person_id not in active_ids for o in observations):
            observations = [
                o.model_copy(update={"decision": "UNCERTAIN", "person_id": None})
                if o.decision == "KNOWN" and o.person_id not in active_ids
                else o
                for o in observations
            ]
        previous_alarm = self.policy.last_alarm
        observed = (
            packet.observed_ms
            if packet.observed_ms is not None
            else packet.received_ms - packet.meta.capture_age_ms
        )
        decisions = self.policy.ingest(packet.frame_id, observed, observations)
        if any(o.geometry_valid for o in observations):
            self.last_presence_ms = now
        for decision in decisions:
            if (
                decision["audible"]
                and self.command
                and self.command["type"] == "STOP_ALARM"
                and self.command_deadline > now
            ):
                decision["audible"], decision["suppression"] = False, "stop_pending"
                self.policy.last_alarm = previous_alarm
            event_id, media_id = str(uuid4()), None
            if packet.jpeg:
                media_id = str(uuid4())
                try:
                    temp = self.runtime / "media" / f"{media_id}.tmp"
                    temp.write_bytes(packet.jpeg)
                    temp.replace(temp.with_suffix(".jpg"))
                except OSError:
                    media_id = None
                    decision["evidence_error"] = "MEDIA_WRITE_FAILED"
            decision.update(
                {
                    "frame_id": packet.frame_id,
                    "gallery_revision": self.gallery_revision,
                    "model_set_id": self.store.manifest["model_set_id"],
                    "calibration_id": self.calibration.get("calibration_id"),
                    "mode_revision": self.mode_revision,
                }
            )
            staged_command = None
            with self.store.connection() as db:
                cursor = db.execute(
                    "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?,?,NULL)",
                    (
                        event_id,
                        decision["visit_id"],
                        decision["reason"],
                        decision["severity"],
                        utcnow(),
                        self.source,
                        json.dumps(decision),
                        media_id,
                    ),
                )
                if cursor.rowcount:
                    if decision["audible"]:
                        staged_command = self._issue("START_ALARM", decision["visit_id"], db)
                        decision["command_id"] = (
                            staged_command["command_id"] if staged_command else None
                        )
                        db.execute(
                            "UPDATE events SET details_json=? WHERE id=?",
                            (json.dumps(decision), event_id),
                        )
                    if self.cfg["cloud"]["events_enabled"]:
                        db.execute(
                            "INSERT OR IGNORE INTO cloud_outbox(event_id) VALUES(?)", (event_id,)
                        )
                    if decision["severity"] in {"warning", "alarm"}:
                        db.execute(
                            "INSERT OR IGNORE INTO outbox VALUES(?,?,'pending',0,?,NULL)",
                            (str(uuid4()), event_id, utcnow()),
                        )
            if staged_command:
                self._deliver_committed(staged_command)
        self.latest_analysis = {
            "frame_id": packet.frame_id,
            "source": self.source,
            "observations": [o.model_dump() for o in observations],
            "processed_at": utcnow(),
        }
        self.last_analysis_ms = now
        self.worker_error = None

    def analysis_metrics(self, now):
        times = self.analysis_times
        span = times[-1] - times[0] if len(times) > 1 else 0
        durations = sorted(self.analysis_durations)
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
        interval = self.cfg["analysis"][
            "enrollment_interval_ms" if self.mode == "ENROLLMENT" else "monitoring_interval_ms"
        ]
        enabled = self.mode == "ARMED" or (self.mode == "ENROLLMENT" and self.video.sampling())
        fresh = bool(enabled and times and now - times[-1] <= max(interval * 2, 3000))
        return {
            "interval_ms": interval,
            "enabled": enabled,
            "effective_fps": round((len(times) - 1) * 1000 / span, 2) if span > 0 and fresh else 0,
            "duration_p95_ms": p95,
            "queue_wait_ms": self.last_ai_queue_ms,
            "stages_ms": self.last_stage_ms,
            "over_budget": bool(p95 is not None and p95 > interval * 0.7),
        }
