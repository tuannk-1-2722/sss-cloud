from __future__ import annotations

import numpy as np


def normalize(vector) -> np.ndarray:
    v = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not np.isfinite(v).all() or np.linalg.norm(v) < 1e-8:
        raise ValueError("Invalid embedding")
    return v / np.linalg.norm(v)


def match(query, gallery: dict[str, list[np.ndarray]], cfg: dict) -> dict:
    if not gallery:
        return {"decision": "UNCERTAIN", "person_id": None, "reason": "EMPTY_GALLERY"}
    q = normalize(query)
    ranked = sorted(
        (
            (pid, max(float(np.dot(q, normalize(g))) for g in samples))
            for pid, samples in gallery.items()
            if samples
        ),
        key=lambda pair: (-pair[1], pair[0]),
    )
    if not ranked:
        return {"decision": "UNCERTAIN", "person_id": None, "reason": "EMPTY_GALLERY"}
    person, first = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else None
    if first >= cfg["accept"] and (second is None or first - second >= cfg["margin"]):
        decision, pid = "KNOWN", person
    elif first < cfg["reject"]:
        decision, pid = "UNKNOWN", None
    else:
        decision, pid = "UNCERTAIN", None
    return {"decision": decision, "person_id": pid, "s1": first, "s2": second}
