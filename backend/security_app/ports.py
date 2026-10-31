"""Interface owned jointly by AI and integration; never return embeddings to the UI."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from .contracts import Observation


class VisionPort(Protocol):
    def detections(self, image: np.ndarray) -> tuple[list, list]: ...

    def reset(self) -> None: ...

    def analyze(
        self, jpeg: bytes, gallery: dict[str, list[np.ndarray]], now: int
    ) -> list[Observation]: ...

    def enrollment_sample(self, jpeg: bytes) -> tuple[np.ndarray | None, dict]: ...
