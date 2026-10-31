from __future__ import annotations

import json
from copy import deepcopy
from uuid import uuid4

import numpy as np
import pytest
from argon2 import PasswordHasher
from security_app.auth import digest
from security_app.config import load_config
from security_app.contracts import Hello
from security_app.service import SecurityService


@pytest.fixture
def cfg(tmp_path):
    config = deepcopy(load_config())
    config["runtime_dir"] = str(tmp_path)
    config["profile"] = "simulation"
    (tmp_path / "credentials.json").write_text(
        json.dumps(
            {
                "username": "admin",
                "password_hash": PasswordHasher().hash("test-password-private"),
                "device_token_hash": digest("device-test-token-private"),
            }
        )
    )
    return config


def seed(service):
    person = service.create_person("SIM enrolled person")["id"]
    vector = np.eye(1, 128, dtype=np.float32).reshape(-1)
    with service.store.connection() as db:
        db.execute(
            "INSERT INTO samples VALUES(?,?,?,?,?,?,1,'synthetic',1,?,?)",
            (
                str(uuid4()),
                person,
                vector.tobytes(),
                128,
                "0",
                "{}",
                "opencv-zoo-cpu-v1",
                "sss-v2-zoo-416-letterbox",
            ),
        )
        for _ in range(4):
            db.execute(
                "INSERT INTO samples SELECT ?,person_id,embedding,dimension,perceptual_hash,"
                "quality_json,round_no,session_id,committed,model_set_id,preprocess_version "
                "FROM samples WHERE person_id=? LIMIT 1",
                (str(uuid4()), person),
            )
        db.execute("UPDATE people SET active=1 WHERE id=?", (person,))
    return person


@pytest.fixture
def service(cfg):
    s = SecurityService(cfg)
    s.test_person_id = seed(s)
    s.hello(
        Hello(
            protocol_version=2,
            boot_id="test-boot-0001",
            firmware_version="test",
            source="simulator",
            psram_bytes=0,
        )
    )
    try:
        yield s
    finally:
        s.close()
