"""Gallery and capture workflows. All shared state belongs to the coordinator.

The coordinator delegates public methods here; use its RLock for every mutation.
Native face extraction belongs to VisionWorker, never to this manager.
"""

from __future__ import annotations

import json
from uuid import uuid4

import numpy as np

from .config import ROOT
from .runtime import DomainError, Packet


class EnrollmentManager:
    def __init__(self, service):
        self.service = service

    def start_capture(self, purpose: str, person_id: str | None = None) -> dict:
        s = self.service
        with s.lock:
            if purpose != "enrollment":
                raise DomainError("PREVIEW_RETIRED_USE_VIDEO_VIEW", 410)
            s._expire_capture()
            if s.mode != "DISARMED" or s.capture_session:
                raise DomainError("CAPTURE_REQUIRES_DISARMED")
            if purpose == "enrollment":
                if not s.vision_ready or s.cfg["profile"] != "hardware":
                    raise DomainError("ENROLLMENT_REQUIRES_REAL_VISION", 503)
                if not any(p["id"] == person_id for p in s.store.people()):
                    raise DomainError("PERSON_NOT_FOUND", 404)
                if any(p["id"] == person_id and p["active"] for p in s.store.people()):
                    raise DomainError("DISABLE_PERSON_BEFORE_REENROLLMENT")
            duration = s.cfg["enrollment"]["session_ms"]
            s.mode_revision += 1
            s.mode = "ENROLLMENT" if purpose == "enrollment" else "DISARMED"
            s.policy.reset()
            s.visit_evidence.clear()
            s.capture_session = {
                "id": str(uuid4()),
                "purpose": purpose,
                "person_id": person_id,
                "deadline": s.now() + duration,
                "round": 1,
                "accepted": [0, 0],
                "rejected": 0,
                "last_sample_ms": 0,
                "last_feedback": "Face the camera; follow the enrollment guide",
            }
            s._issue("STOP_ALARM")
            return s.capture_status()

    def capture_status(self) -> dict:
        s = self.service
        with s.lock:
            s._expire_capture()
            if not s.capture_session:
                return {"active": False}
            return {
                **s.capture_session,
                "active": True,
                "remaining_ms": max(0, s.capture_session["deadline"] - s.now()),
            }

    def _expire_capture(self):
        s = self.service
        if s.capture_session and s.now() >= s.capture_session["deadline"]:
            s._disarm("capture_expired")

    def next_enrollment_round(self, session_id: str) -> dict:
        s = self.service
        with s.lock:
            s._expire_capture()
            capture = s.capture_session
            if not capture or capture["id"] != session_id or capture["purpose"] != "enrollment":
                raise DomainError("ENROLLMENT_NOT_ACTIVE")
            if capture["accepted"][0] < s.cfg["enrollment"]["per_round_min"]:
                raise DomainError("ROUND_ONE_INCOMPLETE", 422)
            if capture["round"] != 1:
                raise DomainError("ALREADY_IN_ROUND_TWO")
            s.mode_revision += 1
            s._cancel_pending()
            capture["round"] = 2
            capture["last_feedback"] = (
                "Round 2: change distance/angle slightly; keep only one person"
            )
            return s.capture_status()

    def create_person(self, name: str) -> dict:
        s = self.service
        with s.lock:
            if len(s.store.people()) >= s.cfg["enrollment"]["max_people"]:
                raise DomainError("PEOPLE_CAPACITY_REACHED", 422)
            pid = s.store.create_person(name.strip())
            s.store.audit("person_created", pid)
            return {"id": pid, "display_name": name.strip(), "active": False}

    def deactivate_person(self, person_id: str, delete: bool = False):
        s = self.service
        with s.lock:
            if not any(p["id"] == person_id for p in s.store.people()):
                raise DomainError("PERSON_NOT_FOUND", 404)
            if delete or (s.capture_session and s.capture_session["person_id"] == person_id):
                s._disarm("person_changed")
            with s.store.connection() as db:
                db.execute("UPDATE people SET active=0 WHERE id=?", (person_id,))
                if delete:
                    # Already in-flight delivery may have left the server; report it as uncertain.
                    db.execute(
                        "UPDATE outbox SET state='cancelled',reason='PERSON_DELETED' WHERE state='pending'"
                    )
                    db.execute(
                        "UPDATE outbox SET state='unknown',reason='PERSON_DELETED_DURING_DELIVERY' WHERE state='sending'"
                    )
                    db.execute("DELETE FROM people WHERE id=?", (person_id,))
                    # Evidence can contain multiple people: purge all images on full deletion.
                    for row in db.execute("SELECT id,details_json FROM events").fetchall():
                        details = json.loads(row["details_json"])
                        if details.get("person_id") == person_id:
                            details["person_id"] = None
                        db.execute(
                            "UPDATE events SET details_json=?,media_id=NULL WHERE id=?",
                            (json.dumps(details), row["id"]),
                        )
                    for path in (s.runtime / "media").glob("*.jpg"):
                        path.unlink(missing_ok=True)
                    s.latest = s.latest_analysis = None
                    s.video.reset()
            s.gallery_revision += 1
            s.policy.reset()
            s.visit_evidence.clear()
            s._issue("STOP_ALARM")
            s.store.audit("person_deleted" if delete else "person_disabled", person_id)
            if not any(p["active"] for p in s.store.people()):
                s._disarm("gallery_empty")

    def _enrollment_result(self, packet: Packet, vector, quality: dict):
        s = self.service
        from .vision import hash_distance

        capture = s.capture_session
        if (
            not capture
            or capture["id"] != packet.meta.capture_session_id
            or packet.meta.mode_revision != s.mode_revision
        ):
            return
        now = s.now()
        if now >= capture["deadline"]:
            s._disarm("capture_expired")
            return
        if vector is None:
            capture["rejected"] += 1
            capture["last_feedback"] = quality["reason"]
            return
        sample_ms = packet.observed_ms if packet.observed_ms is not None else now
        if sample_ms - capture["last_sample_ms"] < s.cfg["enrollment"]["sample_spacing_ms"]:
            capture["last_feedback"] = "WAIT_BETWEEN_SAMPLES"
            return
        with s.store.connection() as db:
            rows = db.execute(
                "SELECT * FROM samples WHERE session_id=?", (capture["id"],)
            ).fetchall()
            for row in rows:
                previous_quality = json.loads(row["quality_json"])
                previous_pose, pose = (
                    previous_quality.get("pose_signature"),
                    quality.get("pose_signature"),
                )
                if hash_distance(row["perceptual_hash"], quality["perceptual_hash"]) <= s.cfg[
                    "enrollment"
                ]["duplicate_hamming_max"] or (
                    previous_pose is not None
                    and pose is not None
                    and np.linalg.norm(np.asarray(previous_pose) - np.asarray(pose))
                    < s.cfg["enrollment"]["diversity_min_delta"]
                ):
                    capture["rejected"] += 1
                    capture["last_feedback"] = "SAMPLE_TOO_SIMILAR_CHANGE_POSE"
                    return
            # Prevent mixing a different person's face into this enrollment session.
            if rows:
                scores = [
                    float(np.dot(vector, np.frombuffer(r["embedding"], np.float32))) for r in rows
                ]
                if max(scores) < s.cfg["vision"]["accept"]:
                    capture["rejected"] += 1
                    capture["last_feedback"] = "SAMPLE_IDENTITY_CONFLICT_RESTART_OR_REVIEW"
                    return
            other_gallery = s.store.gallery()
            for pid, vectors in other_gallery.items():
                if (
                    pid != capture["person_id"]
                    and max(float(np.dot(vector, g)) for g in vectors) >= s.cfg["vision"]["accept"]
                ):
                    capture["rejected"] += 1
                    capture["last_feedback"] = "POSSIBLE_EXISTING_PERSON_REVIEW"
                    return
            round_index = capture["round"] - 1
            if capture["accepted"][round_index] >= s.cfg["enrollment"]["per_round_min"]:
                capture["last_feedback"] = "ROUND_COMPLETE_USE_NEXT_ROUND_OR_COMMIT"
                return
            manifest = json.loads((ROOT / "models/manifest.json").read_text())
            db.execute(
                "INSERT INTO samples VALUES(?,?,?,?,?,?,?,?,0,?,?)",
                (
                    str(uuid4()),
                    capture["person_id"],
                    vector.astype(np.float32).tobytes(),
                    int(vector.size),
                    quality["perceptual_hash"],
                    json.dumps(quality),
                    capture["round"],
                    capture["id"],
                    manifest["model_set_id"],
                    manifest["preprocess_version"],
                ),
            )
            capture["accepted"][round_index] += 1
            capture["last_sample_ms"] = sample_ms
            capture["last_feedback"] = "ACCEPTED"

    def commit_enrollment(self, session_id: str, expected_revision: int) -> dict:
        s = self.service
        with s.lock:
            s._expire_capture()
            capture = s.capture_session
            if expected_revision != s.mode_revision:
                raise DomainError("MODE_REVISION_CONFLICT")
            if not capture or capture["id"] != session_id or capture["purpose"] != "enrollment":
                raise DomainError("ENROLLMENT_NOT_ACTIVE")
            if (
                sum(capture["accepted"]) < s.cfg["enrollment"]["accepted_min"]
                or min(capture["accepted"]) < s.cfg["enrollment"]["per_round_min"]
            ):
                raise DomainError("ENROLLMENT_INCOMPLETE", 422)
            pid = capture["person_id"]
            can_activate = False  # Activation is an explicit operator action after sanity check.
            with s.store.connection() as db:
                db.execute("DELETE FROM samples WHERE person_id=? AND committed=1", (pid,))
                rows = db.execute(
                    "SELECT id,round_no FROM samples WHERE session_id=? ORDER BY id", (session_id,)
                ).fetchall()
                rounds = [[r["id"] for r in rows if r["round_no"] == n] for n in (1, 2)]
                interleaved = [sample for pair in zip(*rounds, strict=False) for sample in pair]
                selected = interleaved[: s.cfg["enrollment"]["gallery_max"]]
                for sample_id in selected:
                    db.execute("UPDATE samples SET committed=1 WHERE id=?", (sample_id,))
                db.execute("DELETE FROM samples WHERE session_id=? AND committed=0", (session_id,))
                db.execute("UPDATE people SET active=? WHERE id=?", (int(can_activate), pid))
            s.gallery_revision += 1
            s._disarm("enrollment_committed")
            return {
                "person_id": pid,
                "active": can_activate,
                "gallery_revision": s.gallery_revision,
                "next": "ARM_WHEN_READY" if can_activate else "CALIBRATE_THEN_ACTIVATE",
            }

    def activate_person(self, person_id: str):
        s = self.service
        with s.lock:
            if s.mode != "DISARMED" or s.capture_session:
                raise DomainError("ACTIVATE_REQUIRES_DISARMED")
            if s.calibration_error:
                raise DomainError(s.calibration_error)
            limit = (
                s.cfg["enrollment"]["max_people"]
                if s.cfg["profile"] == "simulation"
                else min(
                    s.cfg["enrollment"]["max_people"],
                    s.calibration["validated_gallery_limit"],
                )
            )
            people = s.store.people()
            person = next((p for p in people if p["id"] == person_id), None)
            if person is None:
                raise DomainError("PERSON_NOT_FOUND", 404)
            if person["active"]:
                return
            if sum(p["active"] for p in people) >= limit:
                raise DomainError("CALIBRATED_CAPACITY_REACHED", 422)
            try:
                gallery = s.store.gallery(include_drafts=True)
            except ValueError as exc:
                raise DomainError("GALLERY_REENROLL_REQUIRED") from exc
            if len(gallery.get(person_id, [])) < s.cfg["enrollment"]["gallery_min"]:
                raise DomainError("GALLERY_SAMPLES_REQUIRED", 422)
            with s.store.connection() as db:
                db.execute("UPDATE people SET active=1 WHERE id=?", (person_id,))
            s.gallery_revision += 1
            s.policy.reset()
            s.visit_evidence.clear()
            s.store.audit("person_activated", person_id)
