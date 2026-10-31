from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np

from .config import ROOT


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def apply_migrations(db, directory: Path):
    migrations = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    versions = [int(p.name.split("_")[0]) for p in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError("Migrations must be contiguous and uniquely numbered from 001")
    db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    applied = [r[0] for r in db.execute("SELECT version FROM schema_version ORDER BY version")]
    if applied != versions[: len(applied)] or len(applied) > len(versions):
        raise ValueError("Unsupported database schema version")
    for version, path in zip(versions, migrations, strict=True):
        if version in applied:
            continue
        # executescript starts its own transaction; rollback both DDL and version on failure.
        try:
            db.executescript(
                "BEGIN IMMEDIATE;\n"
                + path.read_text(encoding="utf-8")
                + f"\nINSERT OR IGNORE INTO schema_version VALUES({version});\nCOMMIT;"
            )
        except Exception:
            db.rollback()
            raise


class Store:
    def __init__(self, runtime: Path, recover: bool = True):
        self.runtime = runtime
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "media").mkdir(exist_ok=True)
        self.path = runtime / "security.sqlite3"
        self.manifest = json.loads((ROOT / "models/manifest.json").read_text())
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            apply_migrations(db, Path(__file__).with_name("migrations"))
            if recover:
                db.execute("UPDATE commands SET state='cancelled' WHERE state='pending'")
                db.execute("DELETE FROM web_sessions")
                db.execute("DELETE FROM samples WHERE committed=0")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def audit(self, action: str, target: str | None = None):
        with self.connection() as db:
            db.execute(
                "INSERT INTO audit(action,target,created_at) VALUES(?,?,?)",
                (action, target, utcnow()),
            )

    def people(self) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM people ORDER BY created_at")]

    def create_person(self, name: str) -> str:
        pid = str(uuid4())
        with self.connection() as db:
            db.execute("INSERT INTO people VALUES(?,?,0,?,?)", (pid, name, utcnow(), utcnow()))
        return pid

    def gallery(self, include_drafts: bool = False) -> dict[str, list[np.ndarray]]:
        gallery: dict[str, list[np.ndarray]] = {}
        condition = "" if include_drafts else "AND p.active=1"
        with self.connection() as db:
            for r in db.execute(
                f"SELECT s.* FROM samples s JOIN people p ON s.person_id=p.id "
                f"WHERE s.committed=1 {condition}"
            ):
                vector = np.frombuffer(r["embedding"], dtype=np.float32).copy()
                if (
                    r["model_set_id"] != self.manifest["model_set_id"]
                    or r["preprocess_version"] != self.manifest["preprocess_version"]
                    or r["dimension"] != self.manifest["embedding_dimension"]
                    or vector.size != r["dimension"]
                ):
                    raise ValueError("Stored embedding version/dimension mismatch; reenroll")
                if not np.isfinite(vector).all() or not np.isclose(
                    np.linalg.norm(vector), 1, atol=1e-3
                ):
                    raise ValueError("Stored embedding must be finite and normalized; reenroll")
                gallery.setdefault(r["person_id"], []).append(vector)
        return gallery

    def event_rows(self, limit: int = 100) -> list[dict]:
        with self.connection() as db:
            rows = []
            for r in db.execute("SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)):
                item = dict(r)
                item["details"] = json.loads(item.pop("details_json"))
                outbox = db.execute(
                    "SELECT state,attempts,reason FROM outbox WHERE event_id=?", (r["id"],)
                ).fetchone()
                item["notification"] = dict(outbox) if outbox else None
                command = db.execute(
                    "SELECT state,ack_json FROM commands WHERE id=?",
                    (item["details"].get("command_id"),),
                ).fetchone()
                item["command"] = dict(command) if command else None
                rows.append(item)
            return rows

    def cleanup(self, cfg: dict):
        now = datetime.now(timezone.utc)
        evidence_before = (now - timedelta(days=cfg["evidence_days"])).timestamp()
        orphan_before = (now - timedelta(hours=1)).timestamp()
        with self.connection() as db:
            referenced = {
                r[0] for r in db.execute("SELECT media_id FROM events WHERE media_id IS NOT NULL")
            }
        for temporary in (self.runtime / "media").glob("*.tmp"):
            if temporary.stat().st_mtime < orphan_before:
                temporary.unlink(missing_ok=True)
        files = sorted((self.runtime / "media").glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        removed = []
        for f in files:
            orphan = f.stem not in referenced and f.stat().st_mtime < orphan_before
            if orphan or f.stat().st_mtime < evidence_before or total > cfg["media_quota_bytes"]:
                total -= f.stat().st_size
                removed.append(f.stem)
                f.unlink()
        with self.connection() as db:
            for media_id in removed:
                db.execute("UPDATE events SET media_id=NULL WHERE media_id=?", (media_id,))
            db.execute(
                "DELETE FROM events WHERE created_at<?",
                ((now - timedelta(days=cfg["events_days"])).isoformat(),),
            )
            db.execute(
                "DELETE FROM receipts WHERE received_at<?",
                ((now - timedelta(hours=cfg["receipts_hours"])).isoformat(),),
            )
            db.execute(
                "DELETE FROM commands WHERE created_at<? AND state!='pending'",
                ((now - timedelta(days=cfg["events_days"])).isoformat(),),
            )
            db.execute(
                "DELETE FROM audit WHERE created_at<?",
                ((now - timedelta(days=cfg["events_days"])).isoformat(),),
            )
            db.execute(
                "DELETE FROM action_keys WHERE created_at<?",
                ((now - timedelta(hours=24)).isoformat(),),
            )
            db.execute("DELETE FROM web_sessions WHERE expires_at<?", (now.timestamp(),))

    def backup(self, destination: Path):
        with self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
