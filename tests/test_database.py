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
    record = Dialogue("en", state="CONFIRMING", visit=Visit(beneficiary_id="DEMO-SECRET", visit_type="newborn"),
                      notes=["Private synthetic transcript"])
    register.insert(record)
    register.insert(record)
    assert len(register.list()) == 1
    assert b"DEMO-SECRET" not in path.read_bytes()
    assert b"Private synthetic transcript" not in path.read_bytes()
    with register.connection() as db:
        payload = db.execute("SELECT payload FROM visits").fetchone()[0]
    assert b"Private synthetic transcript" in Fernet(key).decrypt(payload)
    assert "transcript" not in register.list()[0]
    purge(path)
    assert register.list() == []
