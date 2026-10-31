"""Conservative bbox association; independent from OpenCV and orchestration."""

from __future__ import annotations

import math
from uuid import uuid4


def intersection_over_union(a, b) -> float:
    x, y = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    area = max(0, right - x) * max(0, bottom - y)
    return area / max(1e-8, a[2] * a[3] + b[2] * b[3] - area)


class ConservativeTracker:
    def __init__(self, config: dict):
        self.cfg = config
        self.targets: dict[str, tuple] = {}

    def reset(self):
        self.targets.clear()

    def assign(self, boxes: list[tuple], now: int, kinds: list[bool] | None = None) -> list[str]:
        kinds = kinds or [True] * len(boxes)
        self.targets = {
            k: v
            for k, v in self.targets.items()
            if now - v[1] <= self.cfg["policy"]["track_gap_ms"]
        }
        costs: dict[tuple[int, str], float] = {}
        for i, box in enumerate(boxes):
            for key, (old, _, kind) in self.targets.items():
                if kinds[i] != kind:
                    continue
                iou = intersection_over_union(box, old)
                distance = math.hypot(
                    box[0] + box[2] / 2 - old[0] - old[2] / 2,
                    box[1] + box[3] / 2 - old[1] - old[3] / 2,
                ) / math.sqrt(2)
                v = self.cfg["vision"]
                if iou >= v["track_iou"] or distance <= v["track_center_distance"]:
                    costs[i, key] = 1 - iou + distance
        ambiguous_i, ambiguous_keys = set(), set()
        margin = self.cfg["vision"]["ambiguity_margin"]
        for i in range(len(boxes)):
            candidates = sorted((value, key) for (index, key), value in costs.items() if index == i)
            if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < margin:
                ambiguous_i.add(i)
                ambiguous_keys.update(key for _, key in candidates[:2])
        for key in self.targets:
            candidates = sorted((value, i) for (i, k), value in costs.items() if k == key)
            if len(candidates) > 1 and candidates[1][0] - candidates[0][0] < margin:
                ambiguous_keys.add(key)
                ambiguous_i.update(i for _, i in candidates[:2])
        result, used = {}, set()
        for (i, key), _ in sorted(costs.items(), key=lambda pair: (pair[1], pair[0])):
            if (
                i not in result
                and key not in used
                and i not in ambiguous_i
                and key not in ambiguous_keys
            ):
                result[i] = key
                used.add(key)
        for key in ambiguous_keys:
            self.targets.pop(key, None)  # Discard old identity/direction continuity conservatively.
        ids = []
        for i, box in enumerate(boxes):
            key = result.get(i, str(uuid4()))
            self.targets[key] = (box, now, kinds[i])
            ids.append(key)
        return ids
