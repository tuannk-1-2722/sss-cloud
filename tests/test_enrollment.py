import hashlib

import numpy as np
import pytest
from security_app.contracts import FrameMeta
from security_app.service import DomainError, Packet


def begin(service):
    # Exercise orchestration with synthetic vectors, not real vision/accuracy.
    service.cfg["profile"] = "hardware"
    person = service.create_person("Unit fixture")["id"]
    capture = service.start_capture("enrollment", person)
    metadata = FrameMeta(
        session_id=service.session_id,
        boot_id=service.boot_id,
        seq=1,
        mode_revision=service.mode_revision,
        purpose="enrollment",
        capture_session_id=capture["id"],
        capture_age_ms=0,
    )
    return person, capture["id"], Packet("unit", metadata, 0, service.gallery_revision)


def add(service, packet, index, monkeypatch, vector=None):
    monkeypatch.setattr("security_app.service.clock_ms", lambda: (index + 1) * 1000)
    vector = vector if vector is not None else np.eye(1, 128, 1, dtype=np.float32).reshape(-1)
    quality = {
        "reason": "OK",
        "perceptual_hash": hashlib.sha256(str(index).encode()).hexdigest()[:16],
    }
    service._enrollment_result(packet, vector, quality)


def test_two_rounds_commit_preserves_both_rounds_and_draft_until_calibration(service, monkeypatch):
    service.cfg["enrollment"]["gallery_max"] = 5
    person, session, packet = begin(service)
    for index in range(5):
        add(service, packet, index, monkeypatch)
    with pytest.raises(DomainError, match="ENROLLMENT_INCOMPLETE"):
        service.commit_enrollment(session, service.mode_revision)
    service.next_enrollment_round(session)
    packet.meta.mode_revision = service.mode_revision
    for index in range(5, 10):
        add(service, packet, index, monkeypatch)
    result = service.commit_enrollment(session, service.mode_revision)
    assert not result["active"] and service.mode == "DISARMED"
    with service.store.connection() as db:
        rounds = [
            r[0] for r in db.execute("SELECT round_no FROM samples WHERE person_id=?", (person,))
        ]
        assert len(rounds) == 5 and min(rounds.count(1), rounds.count(2)) >= 2
        assert not db.execute("SELECT 1 FROM samples WHERE committed=0").fetchone()
    assert not list((service.runtime / "media").iterdir())


def test_duplicate_hash_and_conflicting_identity_are_rejected(service, monkeypatch):
    _, _, packet = begin(service)
    add(service, packet, 0, monkeypatch)
    add(service, packet, 0, monkeypatch)  # Spacing guard first; then duplicate guard.
    monkeypatch.setattr("security_app.service.clock_ms", lambda: 3000)
    quality = {"reason": "OK", "perceptual_hash": hashlib.sha256(b"0").hexdigest()[:16]}
    service._enrollment_result(packet, np.eye(1, 128, 1, dtype=np.float32).reshape(-1), quality)
    assert service.capture_session["last_feedback"] == "SAMPLE_TOO_SIMILAR_CHANGE_POSE"
    add(service, packet, 3, monkeypatch, np.eye(1, 128, 2, dtype=np.float32).reshape(-1))
    assert service.capture_session["last_feedback"] == "SAMPLE_IDENTITY_CONFLICT_RESTART_OR_REVIEW"
    assert service.capture_session["accepted"] == [1, 0]


def test_cancel_purges_staging_and_rejects_stale_commit(service, monkeypatch):
    person, session, packet = begin(service)
    add(service, packet, 0, monkeypatch)
    service.set_mode("DISARMED", service.mode_revision)
    assert person not in service.store.gallery(include_drafts=True)
    with service.store.connection() as db:
        assert not db.execute("SELECT 1 FROM samples WHERE session_id=?", (session,)).fetchone()
    with pytest.raises(DomainError, match="ENROLLMENT_NOT_ACTIVE"):
        service.commit_enrollment(session, service.mode_revision)
