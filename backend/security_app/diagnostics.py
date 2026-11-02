"""Bounded metadata tracing. Call under the service lock; never retain JPEGs/vectors."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import secrets
from collections import Counter, deque

logger = logging.getLogger(__name__)

REASONS = frozenset(
    "FACE_TOO_SMALL LANDMARKS_INVALID FACE_CROP_EMPTY FACE_BLURRED FACE_EXPOSURE "
    "SCENE_TOO_DARK SCENE_EXPOSURE_FAILURE FACE_ASSOCIATION_AMBIGUOUS CAPACITY_EXCEEDED "
    "EMPTY_GALLERY RESULT_TOO_OLD REVISION_CHANGED MODE_CHANGED REPLACED MODEL_CHANGED "
    "OBSERVATION_CLOCK_REGRESSED CAPTURE_TOO_OLD ANALYSIS_INTERRUPTED WORKER_FAILED "
    "EXACTLY_ONE_FACE_REQUIRED FACE_OUTSIDE_GUIDE EMBEDDING_INVALID NO_FACE "
    "NEW_TRACK MATCHED AMBIGUOUS TYPE_CHANGED TRACK_GAP NO_CANDIDATE "
    "IDENTITY_CONFLICT INSUFFICIENT_IDENTITY_EVIDENCE".split()
)
NUMBERS = frozenset(
    "frame_seq observed_ms received_ms capture_age_ms reported_capture_age_ms "
    "validation_ms queue_wait_ms duration_ms result_age_ms mode_revision gallery_revision "
    "face_count person_count min_side_px eye_distance_px blur eye_blur clipped s1 s2 "
    "face_confidence votes known_votes unknown_votes evidence_span_ms".split()
)
STAGES = frozenset(
    "decode_ms person_detection_ms face_detection_ms embedding_ms preprocessing_ms".split()
)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class RecognitionDiagnostics:
    def __init__(self, *, history_limit: int, trace_duration_ms: int):
        self.records: deque = deque(maxlen=history_limit)
        self.trace_duration_ms = trace_duration_ms
        self.counts: Counter = Counter()
        self.sequence = 0
        self.until = 0
        self.key = secrets.token_bytes(32)

    def start(self, now):
        if not logger.hasHandlers():
            logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.INFO)
        self.until = now + self.trace_duration_ms
        self.key = secrets.token_bytes(32)
        self.records.clear()
        self.counts.clear()
        return self.snapshot(now, include_records=False)

    def stop(self):
        self.until = 0

    def alias(self, value):
        return hashlib.blake2b(str(value).encode(), key=self.key, digest_size=8).hexdigest()

    @staticmethod
    def reason(value):
        return value if value in REASONS else "OTHER" if value else None

    def record(self, event, now, **values):
        if event not in {
            "ingress",
            "capture_rejected",
            "processing",
            "processed",
            "cancelled",
            "stale",
            "replaced",
            "failed",
            "analysis",
            "video_receive_timeout",
            "video_ack_timeout",
        }:
            event = "other"
        self.sequence += 1
        self.counts[event] += 1
        row = {"sequence": self.sequence, "at_ms": now, "event": event}
        row.update(
            {key: value for key, value in values.items() if key in NUMBERS and number(value)}
        )
        if values.get("reason"):
            row["reason"] = self.reason(values["reason"])
        if "stages" in values:
            row["stages"] = {
                k: v for k, v in (values["stages"] or {}).items() if k in STAGES and number(v)
            }
        if "observations" in values:
            row["observations"] = []
            for observation in values["observations"][:20]:
                data = (
                    observation.model_dump() if hasattr(observation, "model_dump") else observation
                )
                item = {
                    "track": self.alias(data.get("track_id", "")),
                    "decision": data["decision"]
                    if data.get("decision")
                    in {"KNOWN", "UNKNOWN", "UNCERTAIN", "NO_FACE", "NOT_CHECKED"}
                    else "UNCERTAIN",
                    "reason": self.reason(data.get("reason")),
                }
                if data.get("person_id"):
                    item["candidate"] = self.alias(data["person_id"])
                if type(data.get("geometry_valid")) is bool:
                    item["geometry_valid"] = data["geometry_valid"]
                if type(data.get("identity_eligible")) is bool:
                    item["identity_eligible"] = data["identity_eligible"]
                evidence = data.get("evidence") or {}
                item.update({k: v for k, v in evidence.items() if k in NUMBERS and number(v)})
                for key in ("zone", "stable_zone"):
                    if data.get(key) in {"A", "B", "OUTSIDE"}:
                        item[key] = data[key]
                for key in ("votes", "known_votes", "unknown_votes", "evidence_span_ms"):
                    if number(data.get(key)):
                        item[key] = data[key]
                item["tracking_reason"] = self.reason(data.get("tracking_reason"))
                row["observations"].append(item)
        self.records.append(row)
        if now < self.until:
            logger.info("RECOGNITION_TRACE %s", json.dumps(row, separators=(",", ":")))

    def snapshot(self, now, *, after=0, include_records=True):
        result = {
            "trace_active": now < self.until,
            "remaining_ms": max(0, self.until - now),
            "sequence": self.sequence,
            "counts": dict(self.counts),
        }
        if include_records:
            result["records"] = [row for row in self.records if row["sequence"] > after]
        return result
