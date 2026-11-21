from cryptography.fernet import Fernet

from backend.db import Register, demo_key, purge
from backend.dialogue import Dialogue
from backend.schemas import Visit


def test_encryption_dedup_and_purge(tmp_path):
    path = tmp_path / "visit.sqlite3"
    keyfile = path.with_suffix(".key")
    key = demo_key(keyfile)
    assert key == demo_key(keyfile)
    register = Register(path, key)
    record = Dialogue("en", state="CONFIRMING", visit=Visit(beneficiary_id="9123", visit_type="newborn"),
                      notes=["Private synthetic transcript"])
    register.insert(record)
    register.insert(record)
    assert len(register.list()) == 1
    assert b"9123" not in path.read_bytes()
    assert b"Private synthetic transcript" not in path.read_bytes()
    with register.connection() as db:
        payload = db.execute("SELECT payload FROM visits").fetchone()[0]
    assert b"Private synthetic transcript" in Fernet(key).decrypt(payload)
    assert "transcript" not in register.list()[0]
    purge(path)
    assert register.list() == []


def test_persistent_visit_and_vaccination_followups_are_independent_and_deduplicated(tmp_path):
    from datetime import timedelta

    from backend.scheduling import local_today
    from backend.schemas import VaccinationPlan

    path = tmp_path / "encrypted.sqlite"
    key = Fernet.generate_key()
    due = local_today() + timedelta(days=7)
    visit = Visit(
        beneficiary_id="0123", visit_type="newborn", weight_kg=3.5, next_visit_date=due,
        vaccination_plans=[VaccinationPlan(name="BCG", status="due", due_date=due)],
    )
    store = Register(path, key)
    first = Dialogue("en", visit=visit, state="CONFIRMING")
    store.insert(first)
    store.insert(first)
    store.insert(Dialogue("en", visit=visit, state="CONFIRMING"))
    rows = Register(path, key).list()
    assert len(rows) == 2 and sum(len(row["followups"]) for row in rows) == 2
    assert store.complete_followup(first.draft_id, "vaccination-0")
    by_kind = {item["kind"]: item for row in store.list() for item in row["followups"]}
    assert by_kind["vaccination"]["completed"]
    assert not by_kind["visit"]["completed"]
    assert b"BCG" not in path.read_bytes()


def test_legacy_identifier_and_followup_are_read_without_migration(tmp_path):
    path = tmp_path / "legacy.db"
    store = Register(path, Fernet.generate_key())
    payload = store.encode({"beneficiary_id": "DEMO-OLD", "next_visit_date": "2020-01-01", "followup_completed": False})
    with store.connection() as db:
        db.execute("INSERT INTO visits (id, created_at, payload) VALUES (?, ?, ?)", ("legacy", "2020-01-01", payload))
    record, = store.list()
    assert record["beneficiary_id"] == "DEMO-OLD"
    assert record["followups"][0]["date"] == "2020-01-01"
    with store.connection() as db:
        assert db.execute("SELECT payload FROM visits").fetchone()[0] == payload
    assert store.complete_followup("legacy")


def test_location_is_encrypted_and_old_payloads_are_untouched(tmp_path):
    from backend.schemas import VisitLocation

    path = tmp_path / "location.sqlite"
    key = Fernet.generate_key()
    register = Register(path, key)
    old = register.encode({"beneficiary_id": "9900", "next_visit_date": None})
    with register.connection() as db:
        db.execute("INSERT INTO visits (id, created_at, payload) VALUES (?, ?, ?)", ("old", "2020-01-01", old))

    point = VisitLocation(latitude=12.345678, longitude=76.54321, accuracy_m=12, captured_at="2026-11-22T09:00:00Z")
    register.insert(Dialogue("en", visit=Visit(beneficiary_id="1234", visit_type="other"),
                             state="CONFIRMING", location=point))
    reopened = Register(path, key)
    rows = {record["beneficiary_id"]: record for record in reopened.list()}
    assert rows["1234"]["location"] == point.model_dump(mode="json")
    assert "location" not in rows["9900"]
    assert b"12.345678" not in path.read_bytes()
    with reopened.connection() as db:
        assert db.execute("SELECT payload FROM visits WHERE id = 'old'").fetchone()[0] == old
        assert [row[1] for row in db.execute("PRAGMA table_info(visits)")] == ["id", "created_at", "payload", "owner_id"]



def test_migration_adds_unassigned_owner_to_preexisting_database(tmp_path):
    import json
    import sqlite3

    path = tmp_path / "existing.db"
    key = Fernet.generate_key()
    payload = Fernet(key).encrypt(json.dumps({"beneficiary_id": "1234"}).encode())
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE visits (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload BLOB NOT NULL)")
        db.execute("INSERT INTO visits VALUES (?, ?, ?)", ("legacy", "2020-01-01", payload))
    register = Register(path, key)
    assert register.list(owner_id=1) == []
    assert register.unassigned() == [{"id": "legacy", "created_at": "2020-01-01"}]
    with register.connection() as db:
        assert db.execute("SELECT payload, owner_id FROM visits").fetchone() == (payload, None)
