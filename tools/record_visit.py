"""Record a short real-camera visit through a renewable authenticated video view."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx


def checked(response):
    response.raise_for_status()
    return response.json() if response.content else {}


def capture_visit(
    client,
    *,
    directory,
    seconds,
    participant_alias,
    split,
    kind,
    condition,
    expected_person_id=None,
    face_eligible=False,
    progress=False,
):
    visit_id = str(uuid4())
    folder = directory / visit_id
    frames, seen, view_id = [], set(), None
    timing_basis, origin = "client_poll", None
    try:
        view_id = checked(client.post("video/viewers", json={}))["lease_id"]
        next_renew = time.monotonic() + 5
        folder.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()
        last_remaining = None
        while time.monotonic() - started < seconds:
            remaining = max(0, int(seconds - (time.monotonic() - started)))
            if progress and remaining != last_remaining:
                print(f"Còn {remaining} giây...", flush=True)
                last_remaining = remaining
            if time.monotonic() >= next_renew:
                checked(client.post("video/viewers", json={"lease_id": view_id}))
                next_renew = time.monotonic() + 5
            response = client.get("latest-frame")
            if response.status_code == 404:
                time.sleep(0.25)
                continue
            response.raise_for_status()
            frame_id = response.headers.get("X-Frame-Id")
            if frame_id and frame_id not in seen:
                seen.add(frame_id)
                observed = response.headers.get("X-Observed-Ms")
                received = response.headers.get("X-Received-Ms")
                frame_time = int((time.monotonic() - started) * 1000)
                metadata = {}
                if observed is not None and received is not None:
                    observed, received = int(observed), int(received)
                    if origin is None:
                        origin = observed
                    frame_time = observed - origin
                    if frame_time < 0 or (frames and frame_time <= frames[-1]["time_ms"]):
                        continue
                    metadata["received_ms"] = max(frame_time, received - origin)
                    timing_basis = "server_estimate"
                image = folder / f"{len(frames):04d}.jpg"
                image.write_bytes(response.content)
                frames.append(
                    {
                        "path": image.relative_to(directory).as_posix(),
                        "time_ms": frame_time,
                        **metadata,
                    }
                )
            time.sleep(0.25)
    finally:
        if view_id:
            client.delete(f"video/viewers/{view_id}")
    if len(frames) < 2:
        raise RuntimeError("Insufficient frames; no manifest entry written. Review partial folder.")
    entry = {
        "visit_id": visit_id,
        "participant_alias": participant_alias,
        "split": split,
        "kind": kind,
        "condition": condition,
        "timing_basis": timing_basis,
        "expected_person_id": expected_person_id,
        "face_eligible": face_eligible,
        "frames": frames,
    }
    with (directory / "visits.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Recorded {len(frames)} distinct frames: {split}/{condition} -> {folder}")
    return entry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--countdown", type=int, default=5)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--participant-alias", required=True)
    parser.add_argument("--split", choices=["enrollment", "validation", "test"], required=True)
    parser.add_argument(
        "--kind",
        choices=[
            "known",
            "unknown",
            "unidentified",
            "passerby",
            "far",
            "outgoing",
            "direct-b",
            "empty",
        ],
        required=True,
    )
    parser.add_argument(
        "--condition",
        choices=["clear", "mask", "hand", "glasses", "mixed", "unspecified"],
        default="unspecified",
    )
    parser.add_argument("--consent-recorded", action="store_true")
    parser.add_argument("--expected-person-id")
    parser.add_argument("--face-eligible", action="store_true")
    args = parser.parse_args()
    if not args.consent_recorded:
        parser.error("Recording real images requires participant consent: --consent-recorded")
    if not 0 <= args.countdown <= 15:
        parser.error("Countdown must be 0–15 seconds")
    if not 5 <= args.seconds <= 300:
        parser.error("Record 5-300 seconds; the viewing lease renews automatically")
    if (args.kind == "known") != bool(args.expected_person_id):
        parser.error("Only known visits require --expected-person-id")
    if args.face_eligible and args.kind not in {"known", "unknown"}:
        parser.error("--face-eligible applies to incoming known/unknown visits only")
    password = os.environ.get("SSS_ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    with httpx.Client(
        base_url=args.server.rstrip("/") + "/api/v2/", timeout=3, trust_env=False
    ) as client:
        login = checked(
            client.post("auth/login", json={"username": args.username, "password": password})
        )
        client.headers["X-CSRF-Token"] = login["csrf_token"]
        try:
            status = checked(client.get("status"))
            if status["profile"] != "hardware" or not status["device_online"]:
                parser.error("A hardware camera must be online; synthetic recording is forbidden")
            for count in range(args.countdown, 0, -1):
                print(f"Recording starts in {count} seconds...", flush=True)
                time.sleep(1)
            print(f"RECORDING {args.seconds} seconds; perform the visit now.", flush=True)
            capture_visit(
                client,
                directory=args.directory,
                seconds=args.seconds,
                participant_alias=args.participant_alias,
                split=args.split,
                kind=args.kind,
                condition=args.condition,
                expected_person_id=args.expected_person_id,
                face_eligible=args.face_eligible,
                progress=True,
            )
        finally:
            client.post("auth/logout", json={})


if __name__ == "__main__":
    main()
