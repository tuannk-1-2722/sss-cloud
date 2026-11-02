"""One representative JPEG per active visit, bounded; coordinator lock owns access."""

from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class Evidence:
    jpeg: bytes
    frame_id: str
    observed_ms: int
    mode_revision: int
    gallery_revision: int
    rank: int


class VisitEvidence:
    def __init__(self, limit, max_bytes, prefer_ms):
        self.limit, self.max_bytes = limit, max_bytes
        self.prefer_ms = prefer_ms
        self.frames = OrderedDict()

    def clear(self):
        self.frames.clear()

    def update(self, tracks, observations, packet, observed_ms):
        if not packet.jpeg or len(packet.jpeg) > self.max_bytes:
            return
        for o in observations:
            track = tracks.get(o.track_id)
            if track is None or track.visit_id is None or track.completed:
                continue
            key = track.visit_id
            rank = 3 if o.decision == "KNOWN" else 2 if o.evidence else 1
            old = self.frames.get(key)
            if old and old.rank > rank and observed_ms - old.observed_ms < self.prefer_ms:
                continue
            self.frames[key] = Evidence(
                packet.jpeg,
                packet.frame_id,
                observed_ms,
                packet.meta.mode_revision,
                packet.gallery_revision,
                rank,
            )
            self.frames.move_to_end(key)
            while len(self.frames) > self.limit:
                self.frames.popitem(last=False)

    def take(self, visit_id, mode_revision, gallery_revision):
        frame = self.frames.pop(visit_id, None)
        if (
            frame
            and frame.mode_revision == mode_revision
            and frame.gallery_revision == gallery_revision
        ):
            return frame
        return None
