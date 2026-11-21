import asyncio
import base64
import json
from contextlib import asynccontextmanager
from threading import Event

from cryptography.fernet import Fernet
from tests.support import TestClient

from backend import live


class ProviderDouble:
    def __init__(self):
        self.interrupted = False
        self.closed = False
        self.finished = Event()

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

    async def finish_utterance(self):
        self.finished.set()

    async def transcripts(self):
        assert await self.input.get() == bytes(3200)
        yield "Beneficiary ID 0024", False
        yield "Beneficiary ID 0024, newborn, weight 2.8", True
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
            socket.send_json({"type": "finish_utterance"})
            assert provider.finished.wait(timeout=2)
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
            assert socket.receive_json()["form"]["beneficiary_id"] == "0024"

    assert provider.openings == 3


def test_browser_disconnected_state_cleans_provider_without_writes(tmp_path, monkeypatch, caplog):
    from starlette import websockets as starlette_ws

    disconnected = getattr(starlette_ws, "WebSocketDisconnected", starlette_ws.WebSocketDisconnect)
    provider = ProviderDouble()
    monkeypatch.setattr(live, "load_voice", lambda: provider)
    monkeypatch.setenv("APP_MODE", "real")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "disconnect.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GEMINI_API_KEY", "")
    receive_text = live.WebSocket.receive_text
    send = live.WebSocket.send
    writes_after_disconnect = []
    disconnect_seen = Event()

    async def receive(self):
        raw = await receive_text(self)
        if raw == "simulate-disconnected-state":
            raise disconnected()
        return raw

    async def watched_send(self, message):
        if getattr(self, "_test_disconnected", False):
            writes_after_disconnect.append(message["type"])
        await send(self, message)

    async def marked_receive(self):
        try:
            return await receive(self)
        except disconnected:
            self._test_disconnected = True
            disconnect_seen.set()
            raise

    monkeypatch.setattr(live.WebSocket, "receive_text", marked_receive)
    monkeypatch.setattr(live.WebSocket, "send", watched_send)
    with TestClient(live.create_live_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello"})
            assert socket.receive_json()["type"] == "ready"
            assert socket.receive_json()["type"] == "update"
            assert socket.receive_json()["type"] == "assistant"
            assert socket.receive_json()["type"] == "audio"
            socket.send_text("simulate-disconnected-state")
            assert disconnect_seen.wait(timeout=2)
    assert provider.closed and provider.interrupted
    assert not writes_after_disconnect
    assert "Voice session failed" not in caplog.text


def test_fastapi_prisma_audio_transcript_and_diagnostics(tmp_path, monkeypatch):
    from backend.gnani.provider import PrismaStream

    partial = "Unconfirmed synthetic draft"
    final = "Beneficiary ID 0024, newborn, weight 2.8"
    pcm = b"\x00\x20" * 512

    class PrismaSocket:
        def __init__(self):
            self.events = asyncio.Queue()
            self.silence_frames = 0

        async def send(self, message):
            assert isinstance(message, bytes)
            if message == pcm:
                await self.events.put({"type": "speech_start"})
                await self.events.put({"type": "transcript", "text": partial, "is_final": False})
                await self.events.put({"type": "error", "message": "private upstream response"})
            else:
                assert message == bytes(1024)
                self.silence_frames += 1
                if self.silence_frames == 32:
                    await self.events.put({"type": "processing"})
                    await self.events.put({"type": "transcript", "text": final, "is_final": True})

        def __aiter__(self):
            return self

        async def __anext__(self):
            return json.dumps(await self.events.get())

    class Voice(ProviderDouble):
        @asynccontextmanager
        async def listen(self, _language):
            yield PrismaStream(PrismaSocket())

    monkeypatch.setattr(live, "load_voice", Voice)
    monkeypatch.setenv("APP_MODE", "real")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "provider-pipeline.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GEMINI_API_KEY", "")

    with TestClient(live.create_live_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello"})
            ready = socket.receive_json()
            assert ready["type"] == "ready"
            assert [socket.receive_json()["type"] for _ in range(3)] == ["update", "assistant", "audio"]
            socket.send_json({"type": "audio", "data": base64.b64encode(pcm).decode()})
            assert socket.receive_json() == {"type": "transcript", "text": partial, "final": False}
            assert socket.receive_json() == {"type": "prisma_notice", "code": "utterance_error"}
            socket.send_json({"type": "heartbeat"})
            heartbeat = socket.receive_json()
            assert heartbeat["audio_chunks"] == 1
            assert heartbeat["prisma"]["nonzero_frames"] == 1
            assert heartbeat["prisma"]["partials"] == 1
            assert heartbeat["prisma"]["errors"] == 1
            assert "private upstream response" not in json.dumps(heartbeat)
            assert partial not in json.dumps(heartbeat)
            socket.send_json({"type": "finish_utterance"})
            final_message = {"type": "transcript", "text": final, "final": True, "sequence": 1}
            assert socket.receive_json() == final_message
            assert socket.receive_json()["state"] == "PROCESSING"
            assert socket.receive_json()["state"] == "CONFIRMING"
            assert not client.get("/api/visits").json()
            assert socket.receive_json()["type"] == "assistant"
            assert socket.receive_json()["type"] == "audio"
            socket.send_json({"type": "heartbeat"})
            status = socket.receive_json()
            assert status["final_sequence"] == 1 and status["displayed_final_sequence"] == 0
            assert final not in json.dumps(status)
        with client.websocket_connect("/ws") as resumed:
            resumed.send_json({"type": "hello", "session": ready["session"]})
            assert resumed.receive_json()["resumed"]
            assert resumed.receive_json()["type"] == "update"
            assert resumed.receive_json() == final_message
            resumed.send_json({"type": "transcript_displayed", "sequence": 99})
            resumed.send_json({"type": "heartbeat"})
            assert resumed.receive_json()["displayed_final_sequence"] == 0
            resumed.send_json({"type": "transcript_displayed", "sequence": 1})
            resumed.send_json({"type": "heartbeat"})
            status = resumed.receive_json()
            assert status["type"] == "heartbeat"
            assert status["final_sequence"] == status["displayed_final_sequence"] == 1
            assert final not in json.dumps(status)
            assert not client.get("/api/visits").json()


def test_live_stop_drains_final_and_saves_only_after_explicit_confirmation(tmp_path, monkeypatch):
    class DrainProvider(ProviderDouble):
        async def transcripts(self):
            await self.input.get()
            yield "Beneficiary ID 0054, newborn, weight 2.8", True
            await asyncio.Future()

        async def finish_utterance(self):
            await self.input.put(b"drain")

    monkeypatch.setattr(live, "load_voice", DrainProvider)
    monkeypatch.setenv("APP_MODE", "real")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "drain.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(live.create_live_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello"})
            assert [socket.receive_json()["type"] for _ in range(4)] == ["ready", "update", "assistant", "audio"]
            socket.send_json({"type": "pause_capture"})
            assert socket.receive_json()["type"] == "capture_paused"
            assert socket.receive_json()["final"]
            assert socket.receive_json()["state"] == "PROCESSING"
            draft = socket.receive_json()
            assert draft["form"]["weight_kg"] == 2.8
            assert socket.receive_json()["type"] == "assistant"
            assert not client.get("/api/visits").json()
            socket.send_json({"type": "confirm_visit", "revision": draft["revision"]})
            assert socket.receive_json()["state"] == "SAVED"
        assert [record["beneficiary_id"] for record in client.get("/api/visits").json()] == ["0054"]


def test_live_diagnosis_reply_reaches_voice_and_stop_requires_explicit_save(tmp_path, monkeypatch):
    texts = [
        "Beneficiary ID 0061. General health visit. The patient has sugar and high BP.",
        "Yes, they have.",
        "No.",
    ]
    spoken = []

    class ConversationalProvider(ProviderDouble):
        async def transcripts(self):
            for text in texts:
                await self.input.get()
                yield text, True
            await asyncio.Future()

        async def speak(self, text, language):
            spoken.append(text)
            yield bytes(3200)

    monkeypatch.setattr(live, "load_voice", ConversationalProvider)
    monkeypatch.setenv("APP_MODE", "real")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "conversation.db"))
    monkeypatch.setenv("DATA_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GEMINI_API_KEY", "")

    with TestClient(live.create_live_app()) as client:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello"})
            assert [socket.receive_json()["type"] for _ in range(5)] == ["ready", "update", "assistant", "audio", "audio_end"]
            for text, next_question in zip(texts, ("Diabetes", "Hypertension", "other health concerns")):
                socket.send_json({"type": "audio", "data": base64.b64encode(bytes(3200)).decode()})
                assert socket.receive_json()["text"] == text
                assert socket.receive_json()["state"] == "PROCESSING"
                draft = socket.receive_json()
                assert next_question in socket.receive_json()["text"]
                assert socket.receive_json()["type"] == "audio"
                assert socket.receive_json()["type"] == "audio_end"
                assert next_question in spoken[-1]
                assert not client.get("/api/visits").json()
            socket.send_json({"type": "pause_capture"})
            assert socket.receive_json()["type"] == "capture_paused"
            assert not client.get("/api/visits").json()
            socket.send_json({"type": "confirm_visit", "revision": draft["revision"]})
            assert socket.receive_json()["state"] == "SAVED"

        records = client.get("/api/visits").json()
        assert len(records) == 1
        assert records[0]["reported_conditions"] == [
            {"term": "Diabetes", "source": "patient_reported_previous_diagnosis"},
            {"term": "Hypertension", "source": "patient_reported"},
        ]
