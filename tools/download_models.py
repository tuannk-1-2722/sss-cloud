"""Fetch exact Zoo artifacts, wrapper and licenses, verifying bytes before replacement."""

from __future__ import annotations

import hashlib
import json
import urllib.request
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


def main():
    manifest = json.loads((ROOT / "models/manifest.json").read_text())
    for entry in manifest["artifacts"]:
        fetch(entry["url"], ROOT / "models" / entry["file"], entry["sha256"], entry["size"])
        fetch(
            entry["license_url"],
            ROOT / "models/licenses" / f"{entry['name']}.txt",
            entry["license_sha256"],
        )
    entry = manifest["wrapper"]
    fetch(entry["url"], ROOT / "models" / entry["file"], entry["sha256"])


if __name__ == "__main__":
    main()
