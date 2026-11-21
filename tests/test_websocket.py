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
