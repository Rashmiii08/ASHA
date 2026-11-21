import base64

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.auth import Account, COOKIE, digest
from backend.live import create_live_app
from tests.support import ORIGIN, PASSWORD, WORKER

ADMIN = {**WORKER, "phone": "9876543211"}
SECOND = {**WORKER, "phone": "9876543212"}


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "register.db"))
    with TestClient(create_live_app(), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        client.app.state.accounts.create(Account.model_validate(ADMIN), bootstrap=True)
        yield client


def login(client, worker, password=PASSWORD):
    return client.post("/api/auth/login", json={"phone": worker, "password": password})


def register(client, worker):
    assert client.post("/api/auth/register", json=worker).status_code == 201


def approve(client, *workers):
    assert login(client, ADMIN["phone"]).status_code == 200
    roster = {row["phone"].removeprefix("+91"): row["id"] for row in client.get("/api/auth/workers").json()}
    for worker in workers:
        assert client.patch(f"/api/auth/workers/{roster[worker]}", json={
            "village": "Assigned area", "status": "active",
        }).status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    return roster


def test_immediate_registration_duplicate_and_secure_credentials(app_client):
    client = app_client
    assert client.get("/api/visits").status_code == 401
    assert client.post("/api/auth/register", json={**WORKER, "role": "admin"}).status_code == 422
    register(client, WORKER)
    duplicate = client.post("/api/auth/register", json={**WORKER, "phone": "+91" + WORKER["phone"]})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "This mobile number is already registered."
    assert login(client, WORKER["phone"], "wrong").status_code == 401
    assert client.get("/api/auth/workers").status_code == 401
    response = login(client, "+91" + WORKER["phone"])
    cookie = response.headers["set-cookie"].lower()
    assert all(attribute in cookie for attribute in ["secure", "httponly", "samesite=strict", "path=/"])
    assert response.json()["user"]["village"] == WORKER["village"]
    assert response.json()["user"]["worker_id"].startswith("ASHA-")
    assert client.get("/api/auth/workers").status_code == 403
    assert client.get("http://testserver/api/visits").status_code == 403
    assert client.post("/api/auth/logout", headers={"Origin": "https://example.org"}).status_code == 403
    saved_cookie = client.cookies.get(COOKIE)
    with client.app.state.register.connection() as db:
        hashes = db.execute("SELECT password_hash FROM workers").fetchall()
        session_digest, = db.execute("SELECT digest FROM auth_sessions").fetchone()
    assert all(value[0].startswith("$argon2id$") for value in hashes)
    assert session_digest == digest(saved_cookie) != saved_cookie
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_reset_code_is_admin_issued_single_use_and_revokes_sessions(app_client):
    client = app_client
    register(client, WORKER)
    roster = approve(client, WORKER["phone"])
    assert login(client, WORKER["phone"]).status_code == 200
    token = client.cookies.get(COOKIE)
    forgot = client.post("/api/auth/forgot", json={"phone": WORKER["phone"]})
    assert "code" not in forgot.json()
    worker_id = roster[WORKER["phone"]]
    assert client.post(f"/api/auth/workers/{worker_id}/reset-code").status_code == 403
    assert login(client, ADMIN["phone"]).status_code == 200
    issued = client.post(f"/api/auth/workers/{worker_id}/reset-code")
    assert issued.headers["Cache-Control"] == "no-store"
    code = issued.json()["code"]
    with client.app.state.register.connection() as db:
        stored, = db.execute("SELECT digest FROM password_resets").fetchone()
    assert stored == digest(code) != code
    reset = {"code": code, "password": "synthetic new passphrase", "confirm_password": "synthetic new passphrase"}
    assert client.post("/api/auth/reset", json=reset).status_code == 200
    assert client.post("/api/auth/reset", json=reset).status_code == 422
    assert client.app.state.accounts.session(token) is None
    assert login(client, WORKER["phone"]).status_code == 401
    assert login(client, WORKER["phone"], reset["password"]).status_code == 200


def test_expiry_rate_limit_and_websocket_revocation(app_client):
    client = app_client
    for _ in range(6):
        assert login(client, "9876543299", "invalid").status_code == 401
    assert login(client, "9876543299", "invalid").status_code == 429
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("wss://testserver/ws"):
            pass
    register(client, WORKER)
    approve(client, WORKER["phone"])
    assert login(client, WORKER["phone"]).status_code == 200
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("wss://testserver/ws", headers={"Origin": "https://example.org"}):
            pass
    token = client.cookies.get(COOKIE)
    with client.websocket_connect("wss://testserver/ws") as ws:
        ws.send_json({"type": "hello"})
        draft_id = ws.receive_json()["session"]
        assert [ws.receive_json()["type"] for _ in range(2)] == ["update", "assistant"]
        ws.send_json({"type": "demo_transcript", "text": "Beneficiary ID 3456. General health visit."})
        assert [ws.receive_json()["type"] for _ in range(4)] == ["transcript", "phase", "update", "assistant"]
        with client.app.state.register.connection() as db:
            db.execute("UPDATE auth_sessions SET expires=0 WHERE digest=?", (digest(token),))
        assert client.get("/api/status").status_code == 401
        assert ws.receive_json()["type"] == "auth_expired"
    assert login(client, WORKER["phone"]).status_code == 200
    with client.websocket_connect("wss://testserver/ws") as ws:
        ws.send_json({"type": "hello", "session": draft_id, "review_only": True})
        assert ws.receive_json()["resumed"]
        assert ws.receive_json()["form"]["beneficiary_id"] == "3456"


def test_visit_location_followup_and_drafts_are_isolated(app_client):
    client = app_client
    for worker in (WORKER, SECOND):
        register(client, worker)
    approve(client, WORKER["phone"], SECOND["phone"])
    assert login(client, WORKER["phone"]).status_code == 200
    location = {"latitude": 10.5, "longitude": 76.8, "accuracy_m": 10, "captured_at": "2026-11-22T09:00:00Z"}
    with client.websocket_connect("wss://testserver/ws") as ws:
        ws.send_json({"type": "hello"})
        draft_id = ws.receive_json()["session"]
        assert [ws.receive_json()["type"] for _ in range(2)] == ["update", "assistant"]
        ws.send_json({"type": "set_visit_location", "request_id": 1, "location": location})
        assert ws.receive_json()["type"] == "visit_location"
        ws.send_json({"type": "audio", "data": base64.b64encode(bytes(1024)).decode()})
        ws.send_json({"type": "demo_transcript", "text": "Beneficiary ID 1234. General health visit. Next visit tomorrow."})
        assert [ws.receive_json()["type"] for _ in range(2)] == ["transcript", "phase"]
        revision = ws.receive_json()["revision"]
        assert ws.receive_json()["type"] == "assistant"
        ws.send_json({"type": "confirm_visit", "revision": revision})
        assert ws.receive_json()["state"] == "SAVED"
    record, = client.get("/api/visits").json()
    assert record["location"] == location
    assert login(client, SECOND["phone"]).status_code == 200
    assert client.get("/api/visits").json() == []
    assert client.patch(f"/api/visits/{record['id']}/followup").status_code == 404
    with client.websocket_connect("wss://testserver/ws") as ws:
        ws.send_json({"type": "hello", "session": draft_id})
        assert ws.receive_json()["type"] == "error"
    assert login(client, WORKER["phone"]).status_code == 200
    assert client.patch(f"/api/visits/{record['id']}/followup").status_code == 200


def test_legacy_migration_and_explicit_assignment(app_client):
    client = app_client
    register(client, WORKER)
    roster = approve(client, WORKER["phone"])
    store = client.app.state.register
    payload = store.encode({"beneficiary_id": "1234"})
    with store.connection() as db:
        db.execute("INSERT INTO visits (id, created_at, payload) VALUES (?, ?, ?)", ("legacy", "2020-01-01", payload))
    assert login(client, WORKER["phone"]).status_code == 200
    assert client.get("/api/visits").json() == []
    assignment = {"worker": roster[WORKER["phone"]]}
    assert client.post("/api/auth/legacy-visits/legacy/assign", json=assignment).status_code == 403
    assert login(client, ADMIN["phone"]).status_code == 200
    assert client.get("/api/auth/legacy-visits").json() == [{"id": "legacy", "created_at": "2020-01-01"}]
    assert client.post("/api/auth/legacy-visits/legacy/assign", json=assignment).status_code == 200
    assert client.post("/api/auth/legacy-visits/legacy/assign", json=assignment).status_code == 409
    assert login(client, WORKER["phone"]).status_code == 200
    assert client.get("/api/visits").json()[0]["beneficiary_id"] == "1234"
    with store.connection() as db:
        assert db.execute("SELECT payload FROM visits WHERE id='legacy'").fetchone()[0] == payload


def test_reset_expiration_and_privileged_admin_recovery(app_client):
    client = app_client
    register(client, WORKER)
    workers = approve(client, WORKER["phone"])
    assert client.post("/api/auth/forgot", json={"phone": WORKER["phone"]}).status_code == 200
    assert login(client, ADMIN["phone"]).status_code == 200
    admin_session = client.cookies.get(COOKIE)
    code = client.post(f"/api/auth/workers/{workers[WORKER['phone']]}/reset-code").json()["code"]
    with client.app.state.register.connection() as db:
        db.execute("UPDATE password_resets SET expires=0 WHERE digest=?", (digest(code),))
    password = "synthetic recovery passphrase"
    assert client.post("/api/auth/reset", json={
        "code": code, "password": password, "confirm_password": password,
    }).status_code == 422
    roster = {row["role"]: row["worker_id"] for row in client.app.state.accounts.list_workers()}
    with pytest.raises(ValueError):
        client.app.state.accounts.recover_admin(roster["worker"], password)
    client.app.state.accounts.recover_admin(roster["admin"], password)
    assert client.app.state.accounts.session(admin_session) is None
    assert login(client, ADMIN["phone"], password).status_code == 200


def test_saved_visit_survives_application_restart(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "persistent.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    with TestClient(create_live_app(), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        register(client, WORKER)
        worker, = client.app.state.accounts.list_workers()
        client.app.state.accounts.manage_worker(worker["id"], "active", worker["village"])
        assert login(client, WORKER["phone"]).status_code == 200
        with client.websocket_connect("wss://testserver/ws") as ws:
            ws.send_json({"type": "hello"})
            assert [ws.receive_json()["type"] for _ in range(3)] == ["ready", "update", "assistant"]
            ws.send_json({"type": "demo_transcript", "text": "Beneficiary ID 1234. General health visit."})
            assert [ws.receive_json()["type"] for _ in range(2)] == ["transcript", "phase"]
            revision = ws.receive_json()["revision"]
            assert ws.receive_json()["type"] == "assistant"
            ws.send_json({"type": "confirm_visit", "revision": revision})
            assert ws.receive_json()["state"] == "SAVED"
        original, = client.get("/api/visits").json()
    with TestClient(create_live_app(), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        assert login(client, WORKER["phone"]).status_code == 200
        assert client.get("/api/visits").json() == [original]


def test_registration_validation_reports_phone_and_password_separately(app_client):
    phone_error = app_client.post("/api/auth/register", json={**WORKER, "phone": "987654321"})
    assert phone_error.status_code == 422
    assert phone_error.json()["detail"] == "Enter a 10-digit Indian mobile number, with optional +91."
    short = app_client.post("/api/auth/register", json={**WORKER, "password": "short"})
    assert short.status_code == 422
    assert short.json()["detail"] == "Enter a password of 12–128 characters."


def test_mobile_migration_preserves_owned_visits_and_blocks_shared_numbers(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    from backend.auth import PASSWORDS
    from backend.db import Register

    path = tmp_path / "legacy.db"
    key = Fernet.generate_key()
    store = Register(path, key)
    encrypted_visit = store.encode({"beneficiary_id": "1234"})
    with store.connection() as db:
        db.execute("""CREATE TABLE workers(
            id INTEGER PRIMARY KEY, worker_id TEXT NOT NULL UNIQUE, profile BLOB NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
            reset_requested INTEGER NOT NULL DEFAULT 0
        )""")
        password_hash = PASSWORDS.hash(PASSWORD)
        for worker, phone in [(42, WORKER["phone"]), (43, SECOND["phone"]), (44, "+91" + SECOND["phone"])]:
            profile = store.encode({"full_name": "Synthetic Worker", "phone": phone, "village": "Test area"})
            db.execute("INSERT INTO workers VALUES (?, ?, ?, ?, 'worker', 'active', 0)",
                       (worker, f"LEGACY-{worker}", profile, password_hash))
        db.execute("INSERT INTO visits (id, created_at, payload, owner_id) VALUES (?, ?, ?, ?)",
                   ("legacy", "2020-01-01", encrypted_visit, 42))

    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("DATA_KEY", key.decode())
    with TestClient(create_live_app(), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        assert login(client, SECOND["phone"]).status_code == 409
        assert client.post("/api/auth/register", json=SECOND).status_code == 409
        assert login(client, WORKER["phone"]).json()["user"]["id"] == 42
        assert client.get("/api/visits").json()[0]["beneficiary_id"] == "1234"
        client.app.state.accounts.manage_worker(44, "active", "Test area", "9876543213")
        assert login(client, "9876543213").json()["user"]["id"] == 44
        assert client.get("/api/visits").json() == []
    with store.connection() as db:
        assert db.execute("SELECT payload, owner_id FROM visits").fetchone() == (encrypted_visit, 42)
        assert db.execute("SELECT worker_id FROM workers WHERE id=42").fetchone()[0] == "LEGACY-42"
    assert WORKER["phone"].encode() not in path.read_bytes()


def test_mobile_registration_database_errors_do_not_leak_details(app_client, monkeypatch):
    import sqlite3

    def unavailable(*args):
        raise sqlite3.OperationalError("synthetic internal database detail")

    monkeypatch.setattr(app_client.app.state.accounts, "create", unavailable)
    response = app_client.post("/api/auth/register", json=WORKER)
    assert response.status_code == 503
    assert response.json()["detail"] == "Registration is temporarily unavailable. Please try again."
