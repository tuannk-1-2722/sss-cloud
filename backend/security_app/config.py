from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def ensure_runtime_profile(runtime: Path, profile: str):
    """Call while holding the runtime lock, before any administration/DB mutation."""
    runtime.mkdir(parents=True, exist_ok=True)
    marker = runtime / "profile.json"
    if marker.exists():
        if json.loads(marker.read_text())["profile"] != profile:
            raise ValueError("Runtime belongs to another profile; use a separate directory")
    elif (runtime / "security.sqlite3").exists():
        raise ValueError("Existing runtime lacks a profile marker; review before migration")
    else:
        marker.write_text(json.dumps({"profile": profile}))


def camera_profile_hash(config: dict) -> str:
    # Changing geometry, quality, recognition or temporal policy invalidates calibration.
    keys = (
        "camera",
        "zones",
        "vision",
        "environment",
        "policy",
        "enrollment",
        "limits",
        "analysis",
    )
    body = json.dumps({k: config[k] for k in keys}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def load_config(path: Path | str | None = None) -> dict:
    cfg = yaml.safe_load((ROOT / "configs/default.yaml").read_text(encoding="utf-8"))
    chosen = Path(path or os.environ.get("SSS_CONFIG", ROOT / "configs/local.yaml"))
    if (path is not None or "SSS_CONFIG" in os.environ) and not chosen.is_file():
        raise FileNotFoundError(f"Config not found: {chosen}")
    if chosen.exists():
        override = yaml.safe_load(chosen.read_text(encoding="utf-8")) or {}

        def merge(base, patch):
            if not isinstance(patch, dict):
                raise ValueError("Config must be a mapping")
            for key, value in patch.items():
                if key == "preview_ms" and base is cfg["enrollment"]:
                    continue  # Retired product feature; tolerate an old local config during upgrade.
                if key not in base:
                    raise ValueError(f"Unknown config key: {key}")
                if isinstance(value, dict) and isinstance(base[key], dict):
                    merge(base[key], value)
                else:
                    base[key] = value

        merge(cfg, override)
    validate_config(cfg)
    runtime = Path(os.environ.get("SSS_RUNTIME", cfg["runtime_dir"]))
    cfg["runtime_dir"] = str(runtime if runtime.is_absolute() else ROOT / runtime)
    return cfg


def validate_config(c: dict) -> None:
    from .spatial import Rect

    defaults = yaml.safe_load((ROOT / "configs/default.yaml").read_text(encoding="utf-8"))

    def shape(value, reference, path="config"):
        if isinstance(reference, dict):
            if not isinstance(value, dict) or set(value) != set(reference):
                raise ValueError(f"{path}: missing or unknown keys")
            for key in reference:
                shape(value[key], reference[key], f"{path}.{key}")
        elif isinstance(reference, bool):
            if type(value) is not bool:
                raise ValueError(f"{path}: boolean required")
        elif isinstance(reference, int):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{path}: positive integer required")
        elif isinstance(reference, float):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{path}: finite number required")
        elif isinstance(reference, str):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{path}: nonempty string required")
        elif isinstance(reference, list):
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                raise ValueError(f"{path}: rectangle requires four numbers")
            if any(type(v) not in (int, float) for v in value):
                raise ValueError(f"{path}: rectangle coordinates must be numeric")

    shape(c, defaults)
    if c["diagnostics"]["history_limit"] > 1024 or c["diagnostics"]["trace_duration_ms"] > 300000:
        raise ValueError("Diagnostics history or trace duration exceeds bounds")
    if (
        not 1000 <= c["notifications"]["expiry_ms"] <= 3600000
        or c["notifications"]["max_attempts"] > 10
    ):
        raise ValueError("Notification expiry or retry bound is invalid")
    if c["cloud"]["inference"] not in {"local", "cloud"}:
        raise ValueError("cloud.inference must be local or cloud")
    if not 300 <= c["cloud"]["inference_timeout_ms"] <= 10000:
        raise ValueError("cloud inference timeout out of range")
    if c["profile"] == "simulation" and c["cloud"]["inference"] == "cloud":
        raise ValueError("Cloud inference must be validated on hardware, not simulation")
    if c["profile"] not in {"simulation", "hardware"}:
        raise ValueError("profile must be simulation or hardware")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", c["device_id"]):
        raise ValueError("device_id must be a slug")
    if (c["camera"]["width"], c["camera"]["height"], c["camera"]["jpeg_quality"]) != (640, 480, 12):
        raise ValueError("Firmware requires VGA/JPEG quality 12")
    a, b = (Rect(*c["zones"][k]) for k in ("approach", "doorstep"))
    if a.overlaps(b):
        raise ValueError("Approach and doorstep zones must not overlap")
    v, p, e, alarm, limits = (c[k] for k in ("vision", "policy", "enrollment", "alarm", "limits"))
    Rect(*v["recognition_region"])
    if p["flow_version"] != "immediate-unknown-v1" or p["completed_history_limit"] > 100:
        raise ValueError("Unsupported recognition flow or excessive completed visit history")
    q = c["environment"]
    if not 0 < q["min_median_luma"] < q["low_light_p95"] <= 255:
        raise ValueError("Invalid environment illumination thresholds")
    if not 0 < q["max_clipped_ratio"] <= 1 or not 0 < q["max_bright_ratio"] <= 1:
        raise ValueError("Invalid environment exposure thresholds")
    if not -1 <= v["reject"] < v["accept"] <= 1:
        raise ValueError("reject < accept and both in [-1, 1] required")
    for key in (
        "face_confidence",
        "monitoring_face_confidence",
        "face_nms",
        "person_confidence",
        "person_nms",
        "clipped_fraction_max",
        "track_iou",
        "track_center_distance",
        "ambiguity_margin",
    ):
        if not 0 < v[key] <= 1:
            raise ValueError(f"vision.{key}: must be in (0, 1]")
    if not 0 <= v["margin"] <= 2 or v["enrollment_face_min_px"] < v["face_min_px"]:
        raise ValueError("Invalid margin or enrollment face size")
    if (
        v["monitoring_face_confidence"] > v["face_confidence"]
        or v["monitoring_blur_min"] > v["blur_min"]
    ):
        raise ValueError("Monitoring quality thresholds cannot exceed enrollment thresholds")
    if not 1 <= p["confirm_votes"] <= 128:
        raise ValueError("Invalid evidence count")
    if p["confirmation_span_ms"] > p["vote_window_ms"]:
        raise ValueError("Confirmation duration cannot fit inside evidence window")
    if not 2 <= p["unknown_confirm_votes"] <= 128:
        raise ValueError("UNKNOWN needs at least two distinct images")
    if not 400 <= p["unknown_confirmation_span_ms"] <= p["vote_window_ms"]:
        raise ValueError("UNKNOWN duration must be at least 400ms and fit evidence window")
    if not v["accept"] < p["strong_known_min_score"] <= 1:
        raise ValueError("Strong known score must be stricter than normal acceptance")
    if not v["margin"] <= p["strong_known_min_margin"] <= 2:
        raise ValueError("Strong known margin cannot weaken normal identity separation")
    if not 500 <= p["strong_known_span_ms"] <= p["vote_window_ms"]:
        raise ValueError("Strong known duration must be at least 500ms and fit evidence window")
    video, analysis = c["video"], c["analysis"]
    if not 100 <= analysis["housekeeping_ms"] <= 1000:
        raise ValueError("Housekeeping interval must be between 100 and 1000ms")
    if not 100 <= video["view_interval_ms"] <= video["armed_interval_ms"] <= 1000:
        raise ValueError("Video intervals must be between 100 and 1000ms")
    if not 2 * video["viewer_renew_ms"] < video["viewer_lease_ms"] <= 60000:
        raise ValueError("Viewer renewal needs expiry headroom")
    if video["max_viewers"] > 3 or not 1000 <= video["stale_ms"] <= 5000:
        raise ValueError("Invalid video bounds")
    if not 100 <= analysis["monitoring_interval_ms"] <= 1000:
        raise ValueError("Monitoring interval must be between 100 and 1000ms")
    if not 750 <= analysis["enrollment_interval_ms"] <= 3000:
        raise ValueError("Enrollment interval must be between 750 and 3000ms")
    if not 1 <= e["gallery_min"] <= e["gallery_max"] <= e["accepted_min"]:
        raise ValueError("Invalid gallery limits")
    if e["accepted_min"] != 2 * e["per_round_min"]:
        raise ValueError("Enrollment requires two equal rounds")
    if e["duplicate_hamming_max"] > 64:
        raise ValueError("dHash is 64 bits")
    Rect(*e["face_region"])
    if not 0 < e["diversity_min_delta"] < 0.5:
        raise ValueError("Enrollment diversity threshold out of range")
    if not 0 < alarm["duration_ms"] <= alarm["hard_cap_ms"] or alarm["test_cap_ms"] > 1000:
        raise ValueError("Invalid alarm duration")
    # These are build-time constants in the firmware; never silently ignore overrides.
    fixed = {
        ("alarm", "hard_cap_ms"): 3000,
        ("alarm", "control_timeout_ms"): 2000,
        ("alarm", "control_lost_ms"): 5000,
        ("alarm", "sync_ms"): 1000,
        ("limits", "max_capture_age_ms"): 1000,
        ("limits", "max_frame_bytes"): 524288,
    }
    for (section, key), expected in fixed.items():
        if c[section][key] != expected:
            raise ValueError(f"{section}.{key}: firmware constant must be {expected}")
    if c["camera"]["active_ms"] > c["camera"]["sentinel_ms"]:
        raise ValueError("Active sampling must be at least as frequent as sentinel")
    if any(c["camera"][key] >= 2**31 for key in c["camera"] if key.endswith("_ms")):
        raise ValueError("Firmware intervals must fit signed millis delta")
    if c["camera"]["presence_hold_ms"] > 3000:
        raise ValueError("Firmware presence hold cap is 3000ms")
    if e["session_ms"] >= 2**31:
        raise ValueError("Capture leases must fit signed millis delta")
    if limits["max_decode_width"] < 640 or limits["max_decode_height"] < 480:
        raise ValueError("Decode limits must admit the camera profile")
    n = c["notifications"]
    if not 60 <= n["push_image_ttl_s"] <= 3600:
        raise ValueError("notifications.push_image_ttl_s must be 60..3600")
    if n["push_image_ttl_s"] * 1000 < n["expiry_ms"]:
        raise ValueError("Push image link must outlive the alert expiry")


def calibration_status(config: dict) -> tuple[dict, str | None]:
    path = Path(os.environ.get("SSS_CALIBRATION", ROOT / "configs/calibration.local.json"))
    if not path.exists():
        path = ROOT / "configs/calibration.seed.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    if config["profile"] == "simulation":
        return profile, None  # Never presented as physical calibration.
    if profile.get("state") != "validated":
        return profile, "CALIBRATION_REQUIRED"
    if profile.get("camera_profile_hash") != camera_profile_hash(config):
        return profile, "CAMERA_PROFILE_CHANGED"
    manifest = ROOT / "models/manifest.json"
    if profile.get("model_manifest_sha256") != hashlib.sha256(manifest.read_bytes()).hexdigest():
        return profile, "MODEL_MANIFEST_CHANGED"
    if not profile.get("validation_report") or not profile.get("reviewer"):
        return profile, "CALIBRATION_EVIDENCE_REQUIRED"
    if profile.get("model_set_id") != json.loads(manifest.read_text())["model_set_id"]:
        return profile, "CALIBRATION_MODEL_SET_MISMATCH"
    if not 1 <= profile.get("validated_gallery_limit", 0) <= config["enrollment"]["max_people"]:
        return profile, "CALIBRATION_GALLERY_LIMIT_INVALID"
    report = Path(profile["validation_report"])
    if not (report if report.is_absolute() else ROOT / report).is_file():
        return profile, "CALIBRATION_REPORT_MISSING"
    return profile, None
