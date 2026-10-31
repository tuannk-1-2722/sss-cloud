"""Offline evaluation of recorded visits; never controls a board or validates calibration."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from collections import Counter
from pathlib import Path
from typing import Literal

import numpy as np
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator
from security_app.config import ROOT, camera_profile_hash, ensure_runtime_profile, load_config
from security_app.policy import PolicyEngine
from security_app.storage import Store, utcnow
from security_app.vision import OpenCVVision


class RecordedFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    time_ms: int = Field(ge=0)


class RecordedVisit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visit_id: str = Field(min_length=1, max_length=64)
    participant_alias: str = Field(min_length=1, max_length=64)
    split: Literal["validation", "test"]
    kind: Literal[
        "known", "unknown", "unidentified", "passerby", "far", "outgoing", "direct-b", "empty"
    ]
    expected_person_id: str | None = None
    # Annotate before inference. Only known/unknown incoming visits can be eligible.
    face_eligible: bool
    frames: list[RecordedFrame] = Field(min_length=2)

    @model_validator(mode="after")
    def consistency(self):
        if (self.kind == "known") != bool(self.expected_person_id):
            raise ValueError("Only known visits require expected_person_id")
        if self.face_eligible and self.kind not in {"known", "unknown"}:
            raise ValueError("face_eligible applies only to incoming known/unknown visits")
        times = [f.time_ms for f in self.frames]
        if times != sorted(set(times)):
            raise ValueError("Frame timestamps must be strictly increasing")
        return self


def read_manifest(path: Path) -> list[RecordedVisit]:
    visits = [
        RecordedVisit.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not visits or len({v.visit_id for v in visits}) != len(visits):
        raise ValueError("Manifest must have visits with globally unique visit_id")
    # A frame file must not be reused in a different visit or split.
    seen = set()
    for visit in visits:
        for frame in visit.frames:
            resolved = (path.parent / frame.path).resolve()
            if not resolved.is_relative_to(path.parent.resolve()):
                raise ValueError("Frame paths must stay inside the dataset directory")
            if resolved in seen or not resolved.is_file():
                raise ValueError("Each frame path must exist and occur exactly once")
            seen.add(resolved)
    return visits


def ratio(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def summarize(rows: list[dict]) -> dict:
    known = [r for r in rows if r["kind"] == "known"]
    unknown = [r for r in rows if r["kind"] == "unknown"]
    eligible_unknown = [r for r in unknown if r["face_eligible"]]
    eligible_known = [r for r in known if r["face_eligible"]]
    negative = [r for r in rows if r["kind"] not in {"unknown"}]
    unresolved = [r for r in rows if r["kind"] in {"unidentified", "direct-b"}]
    return {
        "known_correct_visit": ratio(sum(r["correct_known"] for r in known), len(known)),
        "known_wrong_identity": ratio(sum(r["wrong_known"] for r in known), len(known)),
        "known_eligible_correct": ratio(
            sum(r["correct_known"] for r in eligible_known), len(eligible_known)
        ),
        "unknown_accepted_as_known": ratio(
            sum(r["has_known_candidate"] for r in unknown), len(unknown)
        ),
        "unknown_alarm_event_recall_all": ratio(
            sum(r["has_alarm_event"] for r in unknown), len(unknown)
        ),
        "unknown_alarm_event_recall_eligible": ratio(
            sum(r["has_alarm_event"] for r in eligible_unknown), len(eligible_unknown)
        ),
        "false_alarm_visit": ratio(sum(r["has_alarm_event"] for r in negative), len(negative)),
        "unresolved_warning_visit": ratio(
            sum(r["has_warning"] for r in unresolved), len(unresolved)
        ),
        "incoming_direction_coverage": ratio(
            sum(r["incoming_observed"] for r in known + unknown), len(known + unknown)
        ),
    }


def run_visits(visits: list[RecordedVisit], root: Path, cfg: dict, gallery: dict, vision) -> dict:
    rows, durations, content = [], [], hashlib.sha256()
    for visit in visits:
        vision.reset()
        policy = PolicyEngine(cfg)
        decisions, observations_count, candidate_ids = [], Counter(), set()
        incoming = False
        for index, frame in enumerate(visit.frames):
            jpeg = (root / frame.path).read_bytes()
            if len(jpeg) > cfg["limits"]["max_frame_bytes"]:
                raise ValueError("Dataset frame exceeds the real upload limit")
            image = vision.decode(jpeg)
            if image.shape[:2] != (cfg["camera"]["height"], cfg["camera"]["width"]):
                raise ValueError("Dataset must use the calibrated camera resolution")
            content.update(visit.visit_id.encode())
            content.update(str(frame.time_ms).encode())
            content.update(hashlib.sha256(jpeg).digest())
            started = time.perf_counter()
            observations = vision.analyze(jpeg, gallery, frame.time_ms)
            durations.append((time.perf_counter() - started) * 1000)
            observations_count.update(o.decision for o in observations)
            candidate_ids.update(o.person_id for o in observations if o.decision == "KNOWN")
            decisions.extend(
                policy.ingest(f"{visit.visit_id}:{index}", frame.time_ms, observations)
            )
            incoming |= any(t.direction == "INCOMING" for t in policy.tracks.values())
        known_ids = {d.get("person_id") for d in decisions if d["reason"] == "known_visit"}
        rows.append(
            {
                "visit_id": visit.visit_id,
                "participant_alias": visit.participant_alias,
                "kind": visit.kind,
                "face_eligible": visit.face_eligible,
                "frames": len(visit.frames),
                "correct_known": visit.expected_person_id in known_ids,
                "wrong_known": bool(known_ids - {visit.expected_person_id}),
                "has_known": bool(known_ids),
                "has_known_candidate": bool(candidate_ids),
                "has_alarm_event": any(d["reason"] == "unknown_approach" for d in decisions),
                "has_warning": any(d["severity"] == "warning" for d in decisions),
                "incoming_observed": incoming,
                "observation_counts": dict(observations_count),
                "event_counts": dict(Counter(d["reason"] for d in decisions)),
            }
        )
    return {
        "metrics": summarize(rows),
        "rows": rows,
        "frames_sha256": content.hexdigest(),
        "inference_ms": {
            "frames": len(durations),
            "p50": float(np.percentile(durations, 50)),
            "p95": float(np.percentile(durations, 95)),
            "max": max(durations),
            "scope": "CPU inference only; excludes Wi-Fi, queue, sync and physical buzzer",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--split", choices=["validation", "test"], default="validation")
    parser.add_argument("--config")
    args = parser.parse_args()
    cfg = load_config(args.config)
    if cfg["profile"] != "hardware":
        parser.error("Use hardware config and real-camera committed enrollment (drafts allowed)")
    if args.report.exists():
        parser.error("Use a new report path; preserve prior evaluation evidence")
    visits = [v for v in read_manifest(args.manifest) if v.split == args.split]
    if not visits:
        parser.error("Requested split has no visits")
    runtime = Path(cfg["runtime_dir"])
    if not (runtime / "security.sqlite3").is_file():
        parser.error("First collect and commit real enrollment into this hardware runtime")
    # No running service, no commands sent, and no startup recovery mutations.
    with FileLock(str(runtime / "server.lock"), timeout=0):
        ensure_runtime_profile(runtime, cfg["profile"])
        store = Store(runtime, recover=False)
        gallery = store.gallery(include_drafts=True)
        if not gallery or any(
            v.expected_person_id not in gallery for v in visits if v.kind == "known"
        ):
            parser.error("Gallery must contain all known participant IDs")
        vision = OpenCVVision(cfg)
        vision.detections(np.zeros((cfg["camera"]["height"], cfg["camera"]["width"], 3), np.uint8))
        report = run_visits(visits, args.manifest.parent, cfg, gallery, vision)
        report.update(
            {
                "created_at": utcnow(),
                "split": args.split,
                "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
                "model_manifest_sha256": hashlib.sha256(
                    (ROOT / "models/manifest.json").read_bytes()
                ).hexdigest(),
                "camera_profile_hash": camera_profile_hash(cfg),
                "gallery_people": len(gallery),
                "gallery_samples": {pid: len(vectors) for pid, vectors in gallery.items()},
                "machine": {"python": platform.python_version(), "os": platform.platform()},
                "config": {
                    k: cfg[k] for k in ("camera", "zones", "vision", "policy", "enrollment")
                },
                "note": "Offline event evaluation; calibration requires independent review and HIL",
            }
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Evaluation written to {args.report}. No calibration state was changed.")


if __name__ == "__main__":
    main()
