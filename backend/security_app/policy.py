from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from uuid import uuid4

from .contracts import Observation
from .spatial import zone_for


@dataclass
class Track:
    last_seen: int
    geometry_valid: bool = True
    raw_zone: str = "OUTSIDE"
    pending_zone: str = "OUTSIDE"
    pending_count: int = 0
    stable_zone: str = "OUTSIDE"
    direction: str = "UNRESOLVED"
    last_a_ms: int = -100000
    visit_id: str | None = None
    entered_b: int = 0
    b_count: int = 0
    votes: deque = field(default_factory=lambda: deque(maxlen=128))
    last_vote: int = -100000
    confirmed: str = "UNCERTAIN"
    person_id: str | None = None
    last_eligible: int = -100000
    emitted: set[str] = field(default_factory=set)
    silenced: bool = False


class PolicyEngine:
    """Pure state machine; input is observations, output is decisions, never GPIO/network."""

    def __init__(self, config: dict):
        self.config = config
        self.tracks: dict[str, Track] = {}
        self.seen_frames: deque[str] = deque(maxlen=128)
        self.last_alarm = -100000
        self.capacity_episode: str | None = None

    def reset(self) -> None:
        self.tracks.clear()
        self.seen_frames.clear()
        self.capacity_episode = None
        # A new mode must not bypass the global audible cooldown.

    def silence(self, visit_id: str) -> None:
        for track in self.tracks.values():
            if track.visit_id == visit_id:
                track.silenced = True

    def ingest(self, frame_id: str, now: int, observations: list[Observation]) -> list[dict]:
        if frame_id in self.seen_frames:
            return []
        self.seen_frames.append(frame_id)
        p = self.config["policy"]
        for key in list(self.tracks):
            if now - self.tracks[key].last_seen > p["track_gap_ms"]:
                del self.tracks[key]  # Lost continuity: no inherited direction or known identity.
        observed_ids = {o.track_id for o in observations}
        if len(observed_ids) != len(observations):
            raise ValueError("Duplicate track observation")
        for key, track in self.tracks.items():
            if key not in observed_ids:
                track.pending_count = 0
        decisions = []
        relevant = [o for o in observations if zone_for(o.bbox, self.config["zones"]) != "OUTSIDE"]
        if len(relevant) > p["max_simultaneous"]:
            if self.capacity_episode is None:
                self.capacity_episode = str(uuid4())
                decisions.append(
                    {
                        "visit_id": self.capacity_episode,
                        "reason": "capacity_exceeded",
                        "severity": "warning",
                        "audible": False,
                    }
                )
        else:
            self.capacity_episode = None
        for observation in observations:
            existing = self.tracks.get(observation.track_id)
            if existing and existing.geometry_valid != observation.geometry_valid:
                del self.tracks[observation.track_id]
            track = self.tracks.setdefault(
                observation.track_id,
                Track(last_seen=now, geometry_valid=observation.geometry_valid),
            )
            track.last_seen = now
            raw = zone_for(observation.bbox, self.config["zones"])
            track.raw_zone = raw
            if raw == track.pending_zone:
                track.pending_count += 1
            else:
                track.pending_zone, track.pending_count = raw, 1
            if (
                track.pending_count >= self.config["zones"]["stable_observations"]
                and raw != track.stable_zone
            ):
                old = track.stable_zone
                track.stable_zone = raw
                if raw == "B":
                    track.direction = (
                        "INCOMING"
                        if observation.geometry_valid
                        and now - track.last_a_ms <= p["approach_history_ms"]
                        else "UNRESOLVED"
                    )
                    track.visit_id = str(uuid4())
                    track.entered_b, track.b_count = now, 0
                    track.votes.clear()
                    track.confirmed, track.person_id = "UNCERTAIN", None
                    track.last_eligible = -100000
                    track.last_vote = -100000
                    track.emitted.clear()
                    track.silenced = False
                elif raw == "A" and old == "B":
                    track.direction = "OUTGOING"
                    track.votes.clear()
                    track.confirmed, track.person_id = "UNCERTAIN", None
                elif raw == "OUTSIDE":
                    track.direction = "UNRESOLVED"
                    track.votes.clear()
            if raw == "A" and track.stable_zone == "A" and observation.geometry_valid:
                track.last_a_ms = now
            # A stale stable-zone label must not enable recognition outside B.
            if raw != "B" or track.stable_zone != "B" or track.visit_id is None:
                continue
            track.b_count += 1
            self._vote(track, observation, now)
            common = {
                "visit_id": track.visit_id,
                "track_id": observation.track_id,
                "direction": track.direction,
                "person_id": track.person_id,
            }
            if track.confirmed == "KNOWN" and "known_visit" not in track.emitted:
                track.emitted.add("known_visit")
                decisions.append(
                    {**common, "reason": "known_visit", "severity": "info", "audible": False}
                )
            eligible = (
                track.direction == "INCOMING"
                and observation.geometry_valid
                and now - track.entered_b >= p["doorstep_dwell_ms"]
            )
            if (
                track.confirmed == "UNKNOWN"
                and eligible
                and "unknown_approach" not in track.emitted
            ):
                track.emitted.add("unknown_approach")
                suppression = (
                    "silenced"
                    if track.silenced
                    else "cooldown"
                    if now - self.last_alarm < p["cooldown_ms"]
                    else None
                )
                audible = suppression is None
                if audible:
                    self.last_alarm = now
                decisions.append(
                    {
                        **common,
                        "reason": "unknown_approach",
                        "severity": "alarm",
                        "audible": audible,
                        "suppression": suppression,
                    }
                )
            unresolved = (
                not observation.geometry_valid
                or track.direction == "UNRESOLVED"
                or track.confirmed == "UNCERTAIN"
            )
            if (
                unresolved
                and now - track.entered_b >= p["unresolved_warning_ms"]
                and track.b_count >= p["confirm_votes"]
                and "unresolved_presence" not in track.emitted
                and "unknown_approach" not in track.emitted
            ):
                track.emitted.add("unresolved_presence")
                reason = (
                    "geometry_unresolved"
                    if not observation.geometry_valid
                    else "direction_unresolved"
                    if track.direction == "UNRESOLVED"
                    else "identity_unresolved"
                )
                decisions.append(
                    {
                        **common,
                        "reason": "unresolved_presence",
                        "detail": reason,
                        "severity": "warning",
                        "audible": False,
                    }
                )
        return decisions

    def _vote(self, track: Track, observation: Observation, now: int) -> None:
        p = self.config["policy"]
        while track.votes and now - track.votes[0][0] > p["vote_window_ms"]:
            track.votes.popleft()
        if now > track.last_vote:
            kind, pid = observation.decision, observation.person_id
            if kind == "KNOWN" and pid is None:
                kind = "UNCERTAIN"
            if kind == "KNOWN" and (
                (track.confirmed == "KNOWN" and track.person_id != pid)
                or any(v[1] == "KNOWN" and v[2] != pid for v in track.votes)
            ):
                track.votes.clear()
                track.confirmed, track.person_id = "UNCERTAIN", None
                track.last_eligible = -100000
            track.votes.append((now, kind, pid))
            track.last_vote = now
        known_ids = {v[2] for v in track.votes if v[1] == "KNOWN"}
        known_count = sum(v[1] == "KNOWN" for v in track.votes)
        unknown_count = sum(v[1] == "UNKNOWN" for v in track.votes)
        known_times = [v[0] for v in track.votes if v[1] == "KNOWN"]
        unknown_times = [v[0] for v in track.votes if v[1] == "UNKNOWN"]
        known_span = known_times[-1] - known_times[0] if known_times else 0
        unknown_span = unknown_times[-1] - unknown_times[0] if unknown_times else 0
        if (
            len(known_ids) == 1
            and known_count >= p["confirm_votes"]
            and known_span >= p["confirmation_span_ms"]
        ):
            track.confirmed, track.person_id = "KNOWN", next(iter(known_ids))
            track.last_eligible = max(v[0] for v in track.votes if v[1] == "KNOWN")
        elif (
            not known_ids
            and unknown_count >= p["confirm_votes"]
            and unknown_span >= p["confirmation_span_ms"]
        ):
            track.confirmed, track.person_id = "UNKNOWN", None
            track.last_eligible = max(v[0] for v in track.votes if v[1] == "UNKNOWN")
        if now - track.last_eligible > p["identity_fresh_ms"]:
            track.confirmed, track.person_id = "UNCERTAIN", None

    def snapshot(self) -> list[dict]:
        return [
            {
                "track_id": key,
                "zone": t.raw_zone,
                "stable_zone": t.stable_zone,
                "direction": t.direction,
                "visit_id": t.visit_id,
                "identity": t.confirmed,
                "person_id": t.person_id,
                "silenced": t.silenced,
            }
            for key, t in self.tracks.items()
        ]
