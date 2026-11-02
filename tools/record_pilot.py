"""Consent-based three-clip pilot. Records only; never arms, enrolls or changes the server."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import httpx

from .record_visit import capture_visit, checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--participant-alias", default="P01")
    parser.add_argument("--expected-person-id")
    parser.add_argument("--seconds", type=int, default=15)
    parser.add_argument("--consent-recorded", action="store_true")
    parser.add_argument("--reference-only", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not args.consent_recorded or not 5 <= args.seconds <= 30:
        parser.error("Requires --consent-recorded and 5–30 seconds per clip")
    password = getpass.getpass("Admin password (not stored): ")
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
                parser.error("A real hardware camera must be online")
            if status["mode"] != "DISARMED":
                parser.error("Disarm monitoring before private pilot recording")
            people = [p for p in checked(client.get("people")) if p["active"]]
            pid = args.expected_person_id
            if pid is None:
                if len(people) != 1:
                    parser.error("Use --expected-person-id when more than one person is active")
                pid = people[0]["id"]
            if pid not in {p["id"] for p in people}:
                parser.error("Expected person must be active; this tool does not modify enrollment")
            clips = [
                (
                    "enrollment",
                    "clear",
                    "Reference clip: only you, face visible; slowly vary angle/distance.",
                ),
                (
                    "validation",
                    "clear",
                    "New clear-face visit: approach, turn lock, leave; action about 4–5 seconds.",
                ),
                (
                    "validation",
                    "mask",
                    "Wear a medical mask over nose/mouth; repeat the same door action.",
                ),
            ]
            if args.reference_only:
                clips = clips[:1]
            for split, condition, instruction in clips:
                print(instruction, flush=True)
                if split == "enrollment":
                    print(
                        "THAM CHIẾU: đứng trước camera, toàn bộ mặt ở giữa ảnh, nhìn camera và đổi nhẹ góc. Giữ ở đó đủ 15 giây.",
                        flush=True,
                    )
                input("Nhấn Enter khi đã chuẩn bị; bạn có thêm 5 giây để vào vị trí: ")
                capture_visit(
                    client,
                    directory=args.directory,
                    seconds=args.seconds,
                    participant_alias=args.participant_alias,
                    split=split,
                    kind="known",
                    condition=condition,
                    expected_person_id=pid,
                    face_eligible=condition == "clear",
                    progress=True,
                    ready_timeout_s=20,
                    countdown_s=5,
                    require_coverage=True,
                )
        finally:
            client.post("auth/logout", json={})
    print("Pilot saved privately. This is not a holdout or an accuracy certificate.")


if __name__ == "__main__":
    main()
