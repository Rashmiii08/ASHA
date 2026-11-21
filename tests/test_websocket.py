import base64

import pytest
from fastapi.testclient import TestClient

from backend.live import create_live_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "visits.db"))
    monkeypatch.setenv("APP_MODE", "demo")
    with TestClient(create_live_app()) as client:
        yield client


def start(ws, resume=None, language="en"):
    ws.send_json({"type": "hello", "language": language, "session": resume})
    hello = ws.receive_json()
    assert hello["type"] == "ready"
    update = ws.receive_json()
    if not resume:
        assert ws.receive_json()["type"] == "assistant"
    return hello["session"], update


def turn(ws, text):
    ws.send_json({"type": "demo_transcript", "text": text})
    assert ws.receive_json()["type"] == "transcript"
    assert ws.receive_json()["state"] == "PROCESSING"
    update = ws.receive_json()
    assistant = ws.receive_json()
    assert assistant["type"] == "assistant"
    return update


def test_full_workflow(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/status").json()["mode"] == "demo"
    with client.websocket_connect("/ws") as ws:
        token, initial = start(ws)
        assert initial["state"] == "LISTENING"
        ws.send_text("{")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "audio", "data": "###"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "audio", "data": base64.b64encode(bytes(3200)).decode()})
        ws.send_json({"type": "interrupt"})
        assert ws.receive_json()["type"] == "interrupted"
        update = turn(ws, "Beneficiary ID DEMO-42, newborn, temperature 37.6. Next visit 2026-12-01.")
        assert update["state"] == "CONFIRMING"
        assert update["danger_flag"]
        with client.websocket_connect("/ws") as other:
            _, fresh = start(other)
            assert fresh["form"]["beneficiary_id"] is None
    with client.websocket_connect("/ws") as ws:
        _, restored = start(ws, token)
        assert restored["form"]["beneficiary_id"] == "DEMO-42"
        assert turn(ws, "No, temperature is 36.9")["danger_flag"] is False
        assert client.get("/api/visits").json() == []
        assert turn(ws, "Yes, save it.")["state"] == "SAVED"
        assert turn(ws, "Yes, save it.")["state"] == "SAVED"
    records = client.get("/api/visits").json()
    assert len(records) == 1 and records[0]["confirmed"]
    record = records[0]
    assert client.patch(f"/api/visits/{record['id']}/followup").json()["completed"]
    assert client.get("/api/visits").json()[0]["followup_completed"]
    assert client.delete("/api/visits").status_code == 403
    assert client.delete("/api/visits", headers={"X-Confirm-Delete": "synthetic-visits"}).status_code == 200
    assert client.get("/api/visits").json() == []


def test_real_mode_never_uses_demo(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("APP_MODE", "real")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "real.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GNANI_API_KEY", "")
    monkeypatch.setenv("GNANI_PROTOCOL_MODULE", "")
    with TestClient(create_live_app()) as client:
        status = client.get("/api/status").json()
        assert status["mode"] == "real" and not status["voice_ready"]
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "hello"})
            assert ws.receive_json()["fatal"]


def test_pause_preserves_draft_and_explicit_confirmation_is_idempotent(client):
    with client.websocket_connect("/ws") as ws:
        token, _ = start(ws)
        edit = turn(ws, "Beneficiary ID DEMO-42, other visit.")
        ws.send_json({"type": "pause_capture"})
        assert ws.receive_json()["type"] == "capture_paused"
        assert client.get("/api/visits").json() == []

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "session": token, "review_only": True})
        assert ws.receive_json()["resumed"]
        restored = ws.receive_json()
        assert restored == edit
        ws.send_json({"type": "confirm_visit", "revision": restored["revision"] - 1})
        assert ws.receive_json()["type"] == "error"
        assert client.get("/api/visits").json() == []
        for _ in range(2):
            ws.send_json({"type": "confirm_visit", "revision": restored["revision"]})
            assert ws.receive_json()["state"] == "SAVED"
    assert len(client.get("/api/visits").json()) == 1


def test_general_health_utterance_and_draft_edit(client):
    text = "This was a general health visit. The patient has sugar and high BP."
    with client.websocket_connect("/ws") as ws:
        start(ws)
        ws.send_json({"type": "demo_transcript", "text": text})
        assert ws.receive_json()["text"] == text
        assert ws.receive_json()["state"] == "PROCESSING"
        result = ws.receive_json()
        assert result["form"]["visit_type"] == "general_health"
        assert result["form"]["reported_conditions"] == [
            {"term": "Diabetes", "source": "patient_reported"},
            {"term": "Hypertension", "source": "patient_reported"},
        ]
        assert "Diabetes" in ws.receive_json()["text"]
        ws.send_json({"type": "pause_capture"})
        assert ws.receive_json()["type"] == "capture_paused"
        ws.send_json({
            "type": "edit_visit", "revision": result["revision"], "beneficiary_id": "DEMO-70",
            "visit_type": "general_health", "retained_conditions": ["Diabetes"], "diagnosis_answers": {"Diabetes": False},
        })
        edited = ws.receive_json()
        assert edited["state"] == "CONFIRMING"
        assert edited["form"]["reported_conditions"] == [{"term": "Diabetes", "source": "patient_reported"}]
        assert not client.get("/api/visits").json()
        ws.send_json({"type": "confirm_visit", "revision": edited["revision"]})
        assert ws.receive_json()["state"] == "SAVED"
    assert client.get("/api/visits").json()[0]["visit_type"] == "general_health"


def test_save_requires_fields_and_explicit_clarification(client):
    with client.websocket_connect("/ws") as ws:
        _, initial = start(ws)
        ws.send_json({"type": "confirm_visit", "revision": initial["revision"]})
        assert ws.receive_json()["type"] == "update"
        assert ws.receive_json()["message"] == "Enter a beneficiary ID. Select a visit type."
        update = turn(ws, "Beneficiary ID DEMO-71. General health visit. I have sugar.")
        ws.send_json({
            "type": "confirm_visit", "revision": update["revision"], "beneficiary_id": "DEMO-71",
            "visit_type": "general_health", "retained_conditions": ["Diabetes"],
        })
        assert ws.receive_json()["review"]["pending_diagnoses"] == ["Diabetes"]
        assert "Answer the diagnosis questions" in ws.receive_json()["message"]
        assert client.get("/api/visits").json() == []


def test_save_atomic_edits_database_failure_retry_and_history(client, monkeypatch):
    import json
    import sqlite3

    register = client.app.state.register
    insert = register.insert
    seen = []

    def fail_once(dialogue):
        seen.append(dialogue.draft_id)
        if len(seen) == 1:
            raise sqlite3.OperationalError("synthetic internal database error")
        return insert(dialogue)

    monkeypatch.setattr(register, "insert", fail_once)
    original = "Beneficiary ID DEMO-72. General health visit. Weight 60. I have fever."
    with client.websocket_connect("/ws") as ws:
        start(ws)
        draft = turn(ws, original)
        payload = {
            "type": "confirm_visit", "revision": draft["revision"], "beneficiary_id": "DEMO-72",
            "visit_type": "general_health", "weight_kg": 62.5, "temperature_c": 37.2,
            "bp_systolic": 120, "bp_diastolic": 80, "retained_conditions": ["Fever"],
            "added_conditions": ["Difficulty breathing"], "transcript_correction": "Corrected synthetic note.",
        }
        ws.send_json(payload)
        reviewed = ws.receive_json()
        assert reviewed["state"] == "CONFIRMING"
        assert reviewed["form"]["weight_kg"] == 62.5
        assert "draft is still available" in ws.receive_json()["message"]
        assert client.get("/api/visits").json() == []
        ws.send_json({"type": "confirm_visit", "revision": reviewed["revision"]})
        assert ws.receive_json()["state"] == "SAVED"
        ws.send_json(payload)
        assert ws.receive_json()["state"] == "SAVED"
    record, = client.get("/api/visits").json()
    assert record["beneficiary_id"] == "DEMO-72" and record["bp_systolic"] == 120
    assert [condition["term"] for condition in record["reported_conditions"]] == ["Fever", "Difficulty breathing"]
    assert len(seen) == 2 and seen[0] == seen[1]
    assert "transcript_correction" not in record
    with register.connection() as db:
        encrypted, = db.execute("SELECT payload FROM visits").fetchone()
    stored = json.loads(register.cipher.decrypt(encrypted))
    assert stored["transcript"] == [original]
    assert stored["transcript_correction"] == "Corrected synthetic note."
