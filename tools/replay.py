"""Latest-only worker admission replay, using recorded arrivals and measured CPU cost."""

from __future__ import annotations

import math


def replay_latest(frames, analyze, *, interval_ms, max_age_ms):
    outputs, replaced, stale = [], 0, 0
    index, next_start, busy_until = 0, 0, 0
    arrivals = [f.received_ms if f.received_ms is not None else f.time_ms for f in frames]
    if arrivals != sorted(arrivals):
        raise ValueError("Recorded arrival times must be nondecreasing")
    while index < len(frames):
        started = max(next_start, busy_until, arrivals[index])
        selected = index
        while selected + 1 < len(frames) and arrivals[selected + 1] <= started:
            selected += 1
        replaced += selected - index
        index = selected + 1
        if started - frames[selected].time_ms > max_age_ms:
            stale += 1
            continue
        observations, duration = analyze(selected)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("Inference duration must be finite and nonnegative")
        finished = started + duration
        next_start, busy_until = started + interval_ms, finished
        if finished - frames[selected].time_ms > max_age_ms:
            stale += 1
            continue
        outputs.append(
            {
                "index": selected,
                "started_ms": started,
                "finished_ms": finished,
                "observations": observations,
            }
        )
    return {"outputs": outputs, "replaced": replaced, "stale": stale}
