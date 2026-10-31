"""Shared runtime values without model, HTTP or database dependencies."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .contracts import FrameMeta, Observation


def clock_ms() -> int:
    return int(time.monotonic() * 1000)


class DomainError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


@dataclass
class Packet:
    frame_id: str
    meta: FrameMeta
    received_ms: int
    gallery_revision: int
    jpeg: bytes | None = None
    observations: list[Observation] | None = None
    durable_receipt: bool = True
    # Server-relative observation time; receipt time is still used for deadlines.
    observed_ms: int | None = None
