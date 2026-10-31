from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self):
        values = (self.x, self.y, self.w, self.h)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Non-finite rectangle")
        if not (0 <= self.x < 1 and 0 <= self.y < 1 and self.w > 0 and self.h > 0):
            raise ValueError("Invalid normalized rectangle")
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError("Rectangle outside frame")

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def overlaps(self, other: Rect) -> bool:
        return (
            self.x < other.x + other.w
            and other.x < self.x + self.w
            and self.y < other.y + other.h
            and other.y < self.y + self.h
        )


def zone_for(bbox: tuple[float, float, float, float], zones: dict) -> str:
    # Bbox center is an image anchor, NOT a distance estimate or a ground-plane coordinate.
    x, y, w, h = bbox
    for name, label in (("approach", "A"), ("doorstep", "B")):
        if Rect(*zones[name]).contains(x + w / 2, y + h / 2):
            return label
    return "OUTSIDE"
