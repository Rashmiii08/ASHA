import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from .schemas import Visit


def demo_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as key:
            key.write(Fernet.generate_key())
    except FileExistsError:
        pass
    return path.read_bytes()


def scheduled_followups(record):
    if "followups" in record:
        return record["followups"]
    if record.get("next_visit_date"):
        return [{"id": "visit", "kind": "visit", "name": None, "date": record["next_visit_date"],
                 "completed": record.get("followup_completed", False)}]
    return []


def schedule_key(record, followup):
    return (record["beneficiary_id"], followup["kind"], (followup["name"] or "").casefold(), followup["date"])


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
        if dialogue.state != "CONFIRMING" or dialogue.save_blockers():
            raise ValueError("A confirmable draft is required")
        visit = Visit.model_validate(dialogue.visit.model_dump())
        record = {
            **visit.model_dump(mode="json"),
            "transcript": dialogue.notes,
            "transcript_correction": dialogue.transcript_correction,
            "location": dialogue.location.model_dump(mode="json") if dialogue.location else None,
            "reported_conditions": dialogue.condition_details(),
            "danger_flag": bool(dialogue.danger),
            "danger_signs": sorted(dialogue.danger),
            "confirmed": True,
            "followup_completed": False,
        }
        requested = scheduled_followups(record)
        requested.extend(
            {"id": f"vaccination-{index}", "kind": "vaccination", "name": plan["name"],
             "date": plan["due_date"], "completed": False}
            for index, plan in enumerate(record["vaccination_plans"])
            if plan["status"] == "due" and plan["due_date"]
        )
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM visits WHERE id = ?", (dialogue.draft_id,)).fetchone():
                return
            existing = set()
            if requested:
                for (payload,) in db.execute("SELECT payload FROM visits"):
                    saved = json.loads(self.cipher.decrypt(payload))
                    if saved.get("beneficiary_id") == record["beneficiary_id"]:
                        existing.update(schedule_key(saved, item) for item in scheduled_followups(saved))
            record["followups"] = []
            for item in requested:
                key = schedule_key(record, item)
                if key not in existing:
                    record["followups"].append(item)
                    existing.add(key)
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
            record["followups"] = scheduled_followups(record)
            records.append({**record, "id": visit_id, "created_at": created_at})
        return records

    def complete_followup(self, visit_id, followup_id="visit"):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM visits WHERE id = ?", (visit_id,)).fetchone()
            if row is None:
                return False
            record = json.loads(self.cipher.decrypt(row[0]))
            items = scheduled_followups(record)
            selected = next((item for item in items if item["id"] == followup_id), None)
            if selected is None:
                return False
            selected["completed"] = True
            record["followups"] = items
            if followup_id == "visit":
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
