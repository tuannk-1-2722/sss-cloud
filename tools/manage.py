from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import platform
import secrets
from pathlib import Path
from uuid import uuid4

import numpy as np
from argon2 import PasswordHasher
from filelock import FileLock
from security_app.auth import digest
from security_app.config import (
    ROOT,
    calibration_status,
    camera_profile_hash,
    ensure_runtime_profile,
    load_config,
)
from security_app.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "bootstrap",
            "seed-simulation",
            "models-smoke",
            "doctor",
            "calibration-check",
            "backup",
            "restore",
        ],
    )
    parser.add_argument("--config")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--path", type=Path)
    args = parser.parse_args()
    cfg = load_config(args.config)
    runtime = Path(cfg["runtime_dir"])
    runtime.mkdir(parents=True, exist_ok=True)
    if args.command == "doctor":
        import importlib.metadata as metadata

        print(
            json.dumps(
                {
                    "python": platform.python_version(),
                    "os": platform.platform(),
                    "profile": cfg["profile"],
                    "camera_profile_hash": camera_profile_hash(cfg),
                    "packages": {
                        n: metadata.version(n)
                        for n in ("fastapi", "opencv-python-headless", "numpy")
                    },
                },
                indent=2,
            )
        )
        return
    if args.command == "calibration-check":
        profile, error = calibration_status(cfg)
        print(
            json.dumps(
                {
                    "calibration_id": profile.get("calibration_id"),
                    "error": error,
                    "camera_profile_hash": camera_profile_hash(cfg),
                    "model_manifest_sha256": hashlib.sha256(
                        (ROOT / "models/manifest.json").read_bytes()
                    ).hexdigest(),
                },
                indent=2,
            )
        )
        return
    if args.command == "models-smoke":
        from security_app.vision import OpenCVVision

        vision = OpenCVVision(cfg)
        persons, faces = vision.detections(np.zeros((480, 640, 3), np.uint8))
        feature = vision.feature(np.zeros((112, 112, 3), np.uint8))
        print(
            json.dumps(
                {
                    "model_load": "PASS",
                    "dimension": int(feature.size),
                    "blank_persons": len(persons),
                    "blank_faces": len(faces),
                    "note": "Smoke only; no physical-camera accuracy benchmark",
                }
            )
        )
        return
    # Administration mutates disk only while the application is stopped.
    with FileLock(str(runtime / "server.lock"), timeout=0):
        ensure_runtime_profile(runtime, cfg["profile"])
        if args.command == "restore":
            if not args.path or not (args.path / "security.sqlite3").is_file():
                parser.error("--path must name a backup directory")
            if (runtime / "security.sqlite3").exists():
                parser.error("Restore into a fresh SSS_RUNTIME directory")
            import shutil

            manifest = json.loads((args.path / "backup-manifest.json").read_text())
            if manifest.get("profile") != cfg["profile"]:
                raise ValueError("Backup profile differs; never mix hardware and simulation")
            if "security.sqlite3" not in manifest["files"]:
                raise ValueError("Backup manifest must include its database")
            validated = []
            for relative, checksum in manifest["files"].items():
                name = Path(relative)
                if not (
                    name.parts == ("security.sqlite3",)
                    or (
                        len(name.parts) == 2
                        and name.parts[0] == "media"
                        and name.suffix in {".jpg", ".tmp"}
                    )
                ):
                    raise ValueError("Unexpected backup file path")
                source = args.path / relative
                target = runtime / relative
                if not source.resolve().is_relative_to(
                    args.path.resolve()
                ) or not target.resolve().is_relative_to(runtime.resolve()):
                    raise ValueError("Backup paths must remain inside their directories")
                if hashlib.sha256(source.read_bytes()).hexdigest() != checksum:
                    raise ValueError(f"Backup checksum failed: {relative}")
                validated.append((source, target))
            # Validate the whole manifest before starting a restore.
            for source, target in validated:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            restored = Store(runtime)
            with restored.connection() as db:
                db.execute("UPDATE people SET active=0")
            print(
                "Restored inactive gallery. Bootstrap credentials; review consent and reactivate explicitly."
            )
            return
        store = Store(runtime, recover=args.command != "backup")
        if args.command == "bootstrap":
            credentials = runtime / "credentials.json"
            if credentials.exists():
                parser.error("Credentials already exist; move them privately before rotating")
            password = os.environ.get("SSS_BOOTSTRAP_PASSWORD") or getpass.getpass(
                "Admin password (12+ chars): "
            )
            if len(password) < 12:
                parser.error("Use 12+ characters")
            token = secrets.token_urlsafe(32)
            credentials.write_text(
                json.dumps(
                    {
                        "username": args.username,
                        "password_hash": PasswordHasher().hash(password),
                        "device_token_hash": digest(token),
                    }
                )
            )
            token_path = runtime / "device-token.txt"
            token_path.write_text(token)
            credentials.chmod(0o600)
            token_path.chmod(0o600)
            print(
                f"Credentials created. Device token is in {token_path}; do not commit or share it."
            )
        elif args.command == "seed-simulation":
            if cfg["profile"] != "simulation":
                parser.error("Synthetic enrollment is forbidden in hardware profile")
            if store.people():
                parser.error("Use a fresh simulation directory; do not overwrite existing people")
            manifest = json.loads((ROOT / "models/manifest.json").read_text())
            for i, name in enumerate(("SIM - Người quen A", "SIM - Người quen B")):
                pid = store.create_person(name)
                vector = np.eye(1, 128, i, dtype=np.float32).reshape(-1)
                with store.connection() as db:
                    for j in range(5):
                        db.execute(
                            "INSERT INTO samples VALUES(?,?,?,?,?,?,?, ?,1,?,?)",
                            (
                                str(uuid4()),
                                pid,
                                vector.tobytes(),
                                128,
                                f"{j:016x}",
                                "{}",
                                1,
                                "synthetic",
                                manifest["model_set_id"],
                                manifest["preprocess_version"],
                            ),
                        )
                    db.execute("UPDATE people SET active=1 WHERE id=?", (pid,))
            print("Seeded two explicitly synthetic identities. Not a face-recognition test.")
        elif args.command == "backup":
            if not args.path or args.path.exists():
                parser.error("--path must be a new backup directory")
            args.path.mkdir(parents=True)
            store.backup(args.path / "security.sqlite3")
            import shutil

            shutil.copytree(runtime / "media", args.path / "media")
            manifest = {
                "profile": cfg["profile"],
                "files": {
                    str(p.relative_to(args.path)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in args.path.rglob("*")
                    if p.is_file()
                },
            }
            (args.path / "backup-manifest.json").write_text(json.dumps(manifest, indent=2))
            print(
                "Backup complete. Store privately; remove obsolete backups after person deletion."
            )


if __name__ == "__main__":
    main()
