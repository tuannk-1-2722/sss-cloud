"""Exercise real local HTTP with a disposable simulation runtime (about 15 seconds)."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

import httpx
from security_app.config import ROOT


def wait_for(function, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = function()
            if result:
                return result
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("Local HTTP smoke timed out")


def run(root: Path) -> dict:
    runtime = root / "runtime"
    config = root / "config.yaml"
    config.write_text(
        "profile: simulation\nruntime_dir: " + runtime.as_posix() + "\n"
        "notifications:\n  notify_known_visits: true\n  notify_unresolved_visits: true\n"
    )
    password = secrets.token_urlsafe(24)
    env = dict(
        os.environ,
        SSS_CONFIG=str(config),
        SSS_RUNTIME=str(runtime),
        SSS_BOOTSTRAP_PASSWORD=password,
        SSS_CALIBRATION=str(ROOT / "configs/calibration.seed.json"),
        SSS_HTTPS="0",
    )
    for command in ("bootstrap", "seed-simulation"):
        subprocess.run(
            [sys.executable, "-m", "tools.manage", command],
            cwd=ROOT,
            env=env,
            check=True,
            capture_output=True,
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    address = f"http://127.0.0.1:{port}"
    simulator = None
    report = {
        "source": "simulation",
        "physical_buzzer_tested": False,
        "face_accuracy_tested": False,
        "scenarios": [],
    }
    with (root / "server.log").open("w") as server_log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tools.serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=address + "/api/v2/", timeout=2, trust_env=False) as client:
                wait_for(lambda: client.get(address + "/health/ready").status_code == 200)
                login = client.post("auth/login", json={"username": "admin", "password": password})
                login.raise_for_status()
                client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                person = client.get("people").json()[0]["id"]
                from tools.smoke_video import run_video

                report["video"] = run_video(client, address, runtime)
                for scenario, reason in [
                    ("unknown", "unknown_approach"),
                    ("direct-b", "unknown_approach"),
                    ("unidentified", "unresolved_presence"),
                    ("known", "known_visit"),
                ]:
                    baseline = {event["id"] for event in client.get("events").json()}
                    revision = client.get("status").json()["mode_revision"]
                    with (root / f"{scenario}.log").open("w") as simlog:
                        simulator = subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "tools.device_simulator",
                                "--url",
                                address,
                                "--scenario",
                                scenario,
                                "--person-id",
                                person,
                                "--seconds",
                                "20",
                            ],
                            cwd=ROOT,
                            env=env,
                            stdout=simlog,
                            stderr=subprocess.STDOUT,
                        )

                        def new_device(minimum_revision=revision):
                            status = client.get("status").json()
                            return (
                                status
                                if status["device_online"]
                                and status["mode_revision"] > minimum_revision
                                else None
                            )

                        status = wait_for(new_device)
                        client.put(
                            "system/mode",
                            json={"mode": "ARMED", "expected_revision": status["mode_revision"]},
                        ).raise_for_status()
                        event = wait_for(
                            lambda previous=baseline, expected=reason: next(
                                (
                                    event
                                    for event in client.get("events").json()
                                    if event["id"] not in previous and event["reason"] == expected
                                ),
                                None,
                            )
                        )
                        if scenario in {"unknown", "direct-b"}:
                            assert event["details"]["audible"]
                            event_id = event["id"]
                            event = wait_for(
                                lambda wanted=event_id: next(
                                    (
                                        event
                                        for event in client.get("events").json()
                                        if event["id"] == wanted
                                        and event["command"]
                                        and event["command"]["state"] == "acked"
                                    ),
                                    None,
                                )
                            )
                            ack = json.loads(event["command"]["ack_json"])
                            assert ack["buzzer_state"] == "ON"
                            wait_for(
                                lambda: client.get("status").json()["buzzer_reported"] == "OFF",
                                timeout=5,
                            )
                            report["scenarios"].append(
                                {
                                    "scenario": scenario,
                                    "event": reason,
                                    "command": "acked",
                                    "simulated_buzzer_final": "OFF",
                                }
                            )
                        else:
                            assert not event["details"]["audible"]
                            report["scenarios"].append(
                                {"scenario": scenario, "event": reason, "audible": False}
                            )
                        # Disabled channels still retain eligible outbox entries. Check both
                        # queues without contacting a provider, even with legacy flags enabled.
                        with closing(sqlite3.connect(runtime / "security.sqlite3")) as db:
                            for table in ("outbox", "push_outbox"):
                                queued = db.execute(
                                    f"SELECT COUNT(*) FROM {table} WHERE event_id=?", (event["id"],)
                                ).fetchone()[0]
                                assert queued == int(reason == "unknown_approach")
                        report["scenarios"][-1]["notification_eligible"] = (
                            reason == "unknown_approach"
                        )
                        current = client.get("status").json()
                        client.put(
                            "system/mode",
                            json={
                                "mode": "DISARMED",
                                "expected_revision": current["mode_revision"],
                            },
                        ).raise_for_status()
                        simulator.terminate()
                        simulator.wait(timeout=5)
                        simulator = None
                client.post("auth/logout", json={}).raise_for_status()
            report["result"] = "PASS"
            return report
        finally:
            if simulator and simulator.poll() is None:
                simulator.terminate()
                simulator.wait(timeout=5)
            server.terminate()
            server.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error("Preserve old reports; choose a new path")
    with tempfile.TemporaryDirectory(prefix="sss-http-smoke-") as folder:
        result = run(Path(folder))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
