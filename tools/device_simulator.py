"""Simulate the v2 device protocol. Synthetic observations are visibly tagged."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
from security_app.config import load_config
from security_app.contracts import VideoMeta
from security_app.video import pack_video


class VideoSimulator:
    """Generated JPEGs only; one bounded uplink, no claims about real sensor FPS."""

    def __init__(self, base, token, alarm, state, begin):
        self.url = base.replace("https://", "wss://").replace("http://", "ws://") + "/video"
        self.token, self.alarm, self.state, self.begin = token, alarm, state, begin
        self.stop = threading.Event()
        self.thread = threading.Thread(target=lambda: asyncio.run(self.run()), daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=4)

    async def run(self):
        from PIL import Image, ImageDraw
        from websockets.asyncio.client import connect

        sequence = 0
        while not self.stop.is_set():
            if not self.state().get("video", {}).get("enabled"):
                await asyncio.sleep(0.025)
                continue
            try:
                async with connect(
                    self.url,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                    compression=None,
                    proxy=None,
                    max_size=2048,
                    max_queue=2,
                    open_timeout=2,
                    close_timeout=0.2,
                ) as socket:
                    greeting = json.loads(await asyncio.wait_for(socket.recv(), 2))
                    if greeting != {"protocol_version": 1, "window_frames": 2}:
                        raise ValueError("Invalid video protocol")
                    in_flight, next_frame = [], 0
                    while not self.stop.is_set() and self.state().get("video", {}).get("enabled"):
                        now = time.monotonic()
                        state = self.state()
                        if now >= next_frame and len(in_flight) < 2:
                            sequence += 1
                            captured = int((now - self.begin) * 1000)
                            image = Image.new("RGB", (640, 480), "#173b50")
                            ImageDraw.Draw(image).text(
                                (25, 25), f"SIMULATED CAMERA {sequence}", fill="white"
                            )
                            output = io.BytesIO()
                            image.save(output, format="JPEG")
                            sent = int((time.monotonic() - self.begin) * 1000)
                            meta = VideoMeta(
                                protocol_version=1,
                                session_id=self.alarm.session,
                                boot_id=self.alarm.boot,
                                seq=sequence,
                                mode_revision=state["mode_revision"],
                                captured_uptime_ms=captured,
                                sent_uptime_ms=sent,
                                capture_age_ms=sent - captured,
                                capture_session_id=state.get("capture_session_id"),
                            )
                            await socket.send(pack_video(meta, output.getvalue()))
                            in_flight.append((sequence, now))
                            next_frame = now + state["video"]["interval_ms"] / 1000
                        if in_flight and now - in_flight[0][1] > 1.5:
                            raise TimeoutError("Video credit expired")
                        try:
                            ack = json.loads(await asyncio.wait_for(socket.recv(), 0.01))
                            if not in_flight or ack.get("seq") != in_flight[0][0]:
                                raise ValueError("Invalid video ACK")
                            in_flight.pop(0)
                        except TimeoutError:
                            pass
            except Exception as exc:
                print(f"Synthetic video reconnect: {type(exc).__name__}")
                await asyncio.sleep(0.5)


class AlarmDevice:
    """Portable reference command semantics, also exercised by unit tests."""

    def __init__(self):
        self.session = ""
        self.boot = ""
        self.generation = 0
        self.seen: set[str] = set()
        self.deadline = 0
        self.last_control = 0
        self.last_revision = -1

    def tick(self, now: int):
        if now >= self.deadline or now - self.last_control > 5000:
            self.deadline = 0

    @property
    def state(self):
        return "ON" if self.deadline else "OFF"

    def apply(self, response: dict, now: int, rtt: int = 0) -> dict | None:
        self.tick(now)
        if (
            rtt > 2000
            or response["session_id"] != self.session
            or response["mode_revision"] < self.last_revision
        ):
            return None
        self.last_control = now
        if response["mode"] == "ENROLLMENT" or response["mode_revision"] != self.last_revision:
            self.deadline = 0
        self.last_revision = response["mode_revision"]
        command = response.get("command")
        if not command:
            return None
        kind = command["type"]
        invalid = (
            command["session_id"] != self.session
            or command["boot_id"] != self.boot
            or command["generation"] < self.generation
            or command["mode_revision"] != response["mode_revision"]
            or (
                command["generation"] == self.generation
                and bool(self.seen)
                and command["command_id"] not in self.seen
            )
        )
        status, reason = "applied", None
        if invalid:
            status, reason = "rejected", "STALE_COMMAND"
        elif command["command_id"] in self.seen:
            status = "duplicate"
        elif kind == "STOP_ALARM":
            self.deadline = 0
        elif command["remaining_ttl_ms"] - rtt <= 0:
            status, reason = "rejected", "COMMAND_EXPIRED"
        elif (kind == "START_ALARM" and response["mode"] == "ARMED") or (
            kind == "TEST_ALARM"
            and response["mode"] == "DISARMED"
            and not response.get("capture_lease_ms", 0)
        ):
            cap = 1000 if kind == "TEST_ALARM" else 3000
            if not 0 < command["duration_ms"] <= cap:
                status, reason = "rejected", "INVALID_DURATION"
            else:
                self.deadline = now + command["duration_ms"]
        else:
            status, reason = "rejected", "MODE_OR_TYPE_INVALID"
        if status in {"applied", "duplicate"}:
            self.generation = max(self.generation, command["generation"])
            self.seen = {command["command_id"]}
        return {
            "command_id": command["command_id"],
            "generation": command["generation"],
            "status": status,
            "reason": reason,
            "buzzer_state": self.state,
            "executed_uptime_ms": now,
        }


def scenario(name: str, index: int, person_id: str) -> list[dict]:
    # 0.5 s per frame: two stable A samples, then a B visit. Repeats every 12 s.
    frame = index % 24
    if name == "empty" or frame >= 15:
        return []
    a, b, street = (0.10, 0.25, 0.18, 0.50), (0.62, 0.25, 0.18, 0.50), (0.43, 0.01, 0.08, 0.08)
    bbox = a if frame < 3 else b
    if name == "passerby":
        bbox = a
    elif name == "far":
        bbox = street
    elif name == "direct-b":
        bbox = b
    elif name == "outgoing":
        bbox = b if frame < 3 else a
    decision = "KNOWN" if name == "known" else "NO_FACE" if name == "unidentified" else "UNKNOWN"
    results = [
        {
            "track_id": f"sim-{index // 24}-1",
            "bbox": bbox,
            "decision": decision,
            "person_id": person_id if decision == "KNOWN" else None,
        }
    ]
    if name == "mixed":
        results.append(
            {
                "track_id": f"sim-{index // 24}-2",
                "bbox": bbox,
                "decision": "KNOWN",
                "person_id": person_id,
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--scenario",
        choices=[
            "empty",
            "known",
            "unknown",
            "unidentified",
            "mixed",
            "passerby",
            "far",
            "outgoing",
            "direct-b",
        ],
        default="unknown",
    )
    parser.add_argument("--person-id", default="")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--config")
    parser.add_argument(
        "--video", action="store_true", help="Generate visibly synthetic JPEGs on the video uplink"
    )
    args = parser.parse_args()
    cfg = load_config(args.config)
    if cfg["profile"] != "simulation":
        parser.error("This simulator requires simulation profile; use evaluate for recorded JPEGs")
    if args.scenario in {"known", "mixed"} and not args.person_id:
        parser.error("--person-id must be an active simulated person's ID from dashboard")
    token = (Path(cfg["runtime_dir"]) / "device-token.txt").read_text().strip()
    base = f"{args.url}/api/v2/devices/{cfg['device_id']}"
    with httpx.Client(
        headers={"Authorization": f"Bearer {token}"}, timeout=2, trust_env=False
    ) as client:
        boot = str(uuid4())
        response = client.post(
            base + "/hello",
            json={
                "protocol_version": 2,
                "boot_id": boot,
                "firmware_version": "simulator-2.0",
                "source": "simulator",
                "psram_bytes": 0,
            },
        )
        response.raise_for_status()
        alarm = AlarmDevice()
        alarm.boot, alarm.session = boot, response.json()["session_id"]
        begin, sync_seq, frame_seq, acks, frame_index = time.monotonic(), 0, 0, [], 0
        state = {}
        video = VideoSimulator(base, token, alarm, lambda: state, begin) if args.video else None
        next_frame = 0.0
        while time.monotonic() - begin < args.seconds:
            now = int((time.monotonic() - begin) * 1000)
            sync_seq += 1
            alarm.tick(now)
            started = time.monotonic()
            try:
                response = client.post(
                    base + "/sync",
                    json={
                        "session_id": alarm.session,
                        "boot_id": boot,
                        "sync_seq": sync_seq,
                        "uptime_ms": now,
                        "buzzer_state": alarm.state,
                        "acks": acks,
                    },
                )
                response.raise_for_status()
                state = response.json()
                acks = [a for a in acks if a["command_id"] not in state["acked_command_ids"]]
                ack = alarm.apply(
                    state,
                    int((time.monotonic() - begin) * 1000),
                    int((time.monotonic() - started) * 1000),
                )
                if ack:
                    acks = [a for a in acks if a["command_id"] != ack["command_id"]] + [ack]
                    print(json.dumps({"simulated_command": ack["status"], "buzzer": alarm.state}))
                if state["mode"] == "ARMED" and time.monotonic() >= next_frame:
                    frame_seq += 1
                    payload = {
                        "metadata": {
                            "session_id": alarm.session,
                            "boot_id": boot,
                            "seq": frame_seq,
                            "mode_revision": state["mode_revision"],
                            "purpose": "monitoring",
                            "capture_age_ms": 0,
                        },
                        "observations": scenario(args.scenario, frame_index, args.person_id),
                    }
                    response = client.post(base + "/synthetic-frames", json=payload)
                    response.raise_for_status()
                    frame_index += 1
                    next_frame = time.monotonic() + 0.5
            except httpx.HTTPError as exc:
                print(
                    f"Simulator transport error: {type(exc).__name__}; restart after server reboot"
                )
            time.sleep(0.25)
        if video:
            video.close()


if __name__ == "__main__":
    main()
