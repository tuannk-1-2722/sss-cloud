"""Install pinned model artifacts, verifying exact bytes before replacement."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fetch(url: str, path: Path, expected: str, size: int | None = None):
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
        print(f"Verified {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=30) as source, temp.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
        body = temp.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected or (size and len(body) != size):
            raise ValueError(f"CHECKSUM/SIZE FAILED: {path.name}; do not load this artifact")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Downloaded and verified {path.name}")


def extract_verified(archive_path: Path, member: str, path: Path, expected: str, size: int):
    if path.exists() and path.stat().st_size == size:
        if hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            print(f"Verified {path.name}")
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    digest = hashlib.sha256()
    copied = 0
    try:
        with zipfile.ZipFile(archive_path) as archive, archive.open(member) as source, temp.open(
            "wb"
        ) as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
        if digest.hexdigest() != expected or copied != size:
            raise ValueError(f"CHECKSUM/SIZE FAILED: {path.name}; do not load this artifact")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Extracted and verified {path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--insightface-archive",
        type=Path,
        help="Path to smart_security_module.zip supplied with the InsightFace model files",
    )
    args = parser.parse_args()
    manifest = json.loads((ROOT / "models/manifest.json").read_text())
    for entry in manifest["artifacts"]:
        target = ROOT / "models" / entry["file"]
        if "url" in entry:
            fetch(entry["url"], target, entry["sha256"], entry["size"])
        elif "archive_path" in entry:
            if args.insightface_archive is None:
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != entry[
                    "sha256"
                ]:
                    raise ValueError(
                        "Provide --insightface-archive with smart_security_module.zip "
                        "to install the InsightFace weights"
                    )
            else:
                extract_verified(
                    args.insightface_archive,
                    entry["archive_path"],
                    target,
                    entry["sha256"],
                    entry["size"],
                )
        else:
            raise ValueError(f"Model source is not configured: {entry['name']}")
        if "license_url" in entry:
            fetch(
                entry["license_url"],
                ROOT / "models/licenses" / f"{entry['name']}.txt",
                entry["license_sha256"],
            )
    entry = manifest["wrapper"]
    fetch(entry["url"], ROOT / "models" / entry["file"], entry["sha256"])


if __name__ == "__main__":
    main()
