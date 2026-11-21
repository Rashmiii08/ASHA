import asyncio
import base64
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend import live


class ProviderDouble:
    def __init__(self):
        self.interrupted = False
        self.closed = False

    @asynccontextmanager
    async def listen(self, language):
        assert language == "en"
        self.input = asyncio.Queue()
        try:
            yield self
        finally:
            self.closed = True

    async def send_pcm(self, pcm):
        await self.input.put(pcm)

    async def transcripts(self):
        assert await self.input.get() == bytes(3200)
        yield "Beneficiary ID DEMO-24", False
        yield "Beneficiary ID DEMO-24, newborn, weight 2.8", True
        await asyncio.Future()

    async def speak(self, text, language):
        assert language == "en"
        try:
            yield bytes(3200)
            await asyncio.Future()
        finally:
            self.interrupted = True


def test_stream_and_interrupt_with_provider_double(tmp_path, monkeypatch):
    provider = ProviderDouble()
    monkeypatch.setattr(live, "load_voice", lambda: provider)
    monkeypatch.setenv("APP_MODE", "real")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "voice.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(live.create_live_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello"})
            assert socket.receive_json()["type"] == "ready"
            assert socket.receive_json()["state"] == "LISTENING"
            assert "I am an AI assistant" in socket.receive_json()["text"]
            first = socket.receive_json()
            assert first["type"] == "audio" and first["is_first_chunk"]
            assert base64.b64decode(first["data"]) == bytes(3200)
            socket.send_json({"type": "interrupt"})
            assert socket.receive_json()["generation"] > first["generation"]
            assert provider.interrupted
            socket.send_json({"type": "audio", "data": base64.b64encode(bytes(3200)).decode()})
            assert socket.receive_json()["final"] is False
            assert socket.receive_json()["final"] is True
            assert socket.receive_json()["state"] == "PROCESSING"
            update = socket.receive_json()
            assert update["state"] == "CONFIRMING" and update["form"]["weight_kg"] == 2.8
    assert provider.closed


def test_provider_reconnect_preserves_draft_and_waits_for_stream(tmp_path, monkeypatch):
    class ReconnectingProvider(ProviderDouble):
        openings = 0

        @asynccontextmanager
        async def listen(self, language):
            self.openings += 1
            if self.openings == 2:
                raise ConnectionError("mock connection failure")
            async with super().listen(language) as stream:
                yield stream

        async def speak(self, text, language):
            yield bytes(3200)

    provider = ReconnectingProvider()
    monkeypatch.setattr(live, "load_voice", lambda: provider)
    monkeypatch.setenv("APP_MODE", "real")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "reconnect.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GEMINI_API_KEY", "")

    with TestClient(live.create_live_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello"})
            token = socket.receive_json()["session"]
            socket.send_json({"type": "audio", "data": base64.b64encode(bytes(3200)).decode()})
            while (message := socket.receive_json())["type"] != "update" or message["state"] != "CONFIRMING":
                pass

        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "session": token})
            failure = socket.receive_json()
            assert failure["type"] == "error" and not failure["fatal"]

        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "session": token})
            ready = socket.receive_json()
            assert ready["type"] == "ready" and ready["resumed"] and ready["session"] == token
            assert socket.receive_json()["form"]["beneficiary_id"] == "DEMO-24"

    assert provider.openings == 3
