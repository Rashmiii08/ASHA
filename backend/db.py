import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet


def demo_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as key:
            key.write(Fernet.generate_key())
    except FileExistsError:
        pass
    return path.read_bytes()


class Register:
    def __init__(self, path: Path, key: bytes):
        self.path = path
        self.cipher = Fernet(key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS visits (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload BLOB NOT NULL
                )
            """)
            # Detect a configuration mismatch before writing with a different key.
            first = db.execute("SELECT payload FROM visits LIMIT 1").fetchone()
            if first:
                self.cipher.decrypt(first[0])

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA secure_delete = ON")
            with db:
                yield db
        finally:
            db.close()

    def encode(self, record):
        return self.cipher.encrypt(json.dumps(record, ensure_ascii=False).encode())

    def insert(self, dialogue):
        if dialogue.state != "CONFIRMING":
            raise ValueError("A confirmable draft is required")
        record = {
            **dialogue.visit.model_dump(mode="json"),
            "transcript": dialogue.notes,
            "transcript_correction": dialogue.transcript_correction,
            "reported_conditions": dialogue.condition_details(),
            "danger_flag": bool(dialogue.danger),
            "danger_signs": sorted(dialogue.danger),
            "confirmed": True,
            "followup_completed": False,
        }
        with self.connection() as db:
            db.execute("INSERT INTO visits VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING", (
                dialogue.draft_id, datetime.now(timezone.utc).isoformat(), self.encode(record),
            ))

    def list(self):
        with self.connection() as db:
            rows = db.execute("SELECT id, created_at, payload FROM visits ORDER BY created_at DESC").fetchall()
        records = []
        for visit_id, created_at, payload in rows:
            record = json.loads(self.cipher.decrypt(payload))
            record.pop("transcript", None)
            record.pop("transcript_correction", None)
            records.append({**record, "id": visit_id, "created_at": created_at})
        return records

    def complete_followup(self, visit_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM visits WHERE id = ?", (visit_id,)).fetchone()
            if row is None:
                return False
            record = json.loads(self.cipher.decrypt(row[0]))
            if not record["next_visit_date"]:
                return False
            record["followup_completed"] = True
            db.execute("UPDATE visits SET payload = ? WHERE id = ?", (self.encode(record), visit_id))
            return True


def delete_visits(db, visit_id=None):
    if visit_id is None:
        db.execute("DELETE FROM visits")
    else:
        db.execute("DELETE FROM visits WHERE id = ?", (visit_id,))


def purge(path: Path):
    if not path.is_file():
        return
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA secure_delete = ON")
        delete_visits(db)
        db.execute("VACUUM")
    finally:
        db.close()
