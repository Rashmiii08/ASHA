import asyncio
import base64
import json
from contextlib import asynccontextmanager

import pytest

from backend.gnani import provider


@pytest.mark.parametrize(
    "service,url,header",
    [
        ("prisma", "wss://api.vachana.ai/stt/v3/stream", "x-api-key-id"),
        ("timbre", "wss://api.vachana.ai/api/v1/tts", "X-API-Key-ID"),
    ],
)
def test_uses_confirmed_url_and_header(monkeypatch, service, url, header):
    socket = object()
    closed = []

    @asynccontextmanager
    async def connect(target, *, additional_headers, open_timeout, close_timeout):
        assert target == url
        assert additional_headers[header] == "synthetic-test-key"
        if service == "prisma":
            assert additional_headers == {
                header: "synthetic-test-key", "language_code": "en-IN", "model": "prisma-v3.0",
                "x-sample-rate": "16000", "format": "transcribe",
            }
        else:
            assert additional_headers == {header: "synthetic-test-key"}
        assert open_timeout == 10
        assert close_timeout == 2
        try:
            yield socket
        finally:
            closed.append(True)

    monkeypatch.setattr(provider, "connect", connect)

    async def exercise():
        transport = provider.GnaniTransport("synthetic-test-key")
        async with getattr(transport, service)() as opened:
            assert opened is socket

    asyncio.run(exercise())
    assert closed == [True]


@pytest.mark.parametrize("failure", [OSError, TimeoutError])
def test_connection_failure_has_safe_message(monkeypatch, failure):
    @asynccontextmanager
    async def connect(*args, **kwargs):
        raise failure("synthetic private diagnostic")
        yield

    monkeypatch.setattr(provider, "connect", connect)

    async def exercise():
        with pytest.raises(provider.GnaniConnectionError, match=r"^Gnani Prisma WebSocket connection failed\.$"):
            async with provider.GnaniTransport("synthetic-test-key").prisma():
                pytest.fail("Connection unexpectedly succeeded")

    asyncio.run(exercise())


def test_cancellation_closes_socket(monkeypatch):
    async def exercise():
        opened = asyncio.Event()
        closed = asyncio.Event()

        @asynccontextmanager
        async def connect(*args, **kwargs):
            try:
                yield object()
            finally:
                closed.set()

        monkeypatch.setattr(provider, "connect", connect)

        async def speaking():
            async with provider.GnaniTransport("synthetic-test-key").timbre():
                opened.set()
                await asyncio.Future()

        task = asyncio.create_task(speaking())
        await asyncio.wait_for(opened.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(exercise())


class MockSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    async def recv(self):
        return await anext(self)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = next(self.messages, None)
        if message is None:
            raise StopAsyncIteration
        return json.dumps(message)

    async def send(self, data):
        self.sent.append(data)


def test_prisma_acknowledgement_binary_frames_and_final_transcript(monkeypatch):
    socket = MockSocket([
        {"type": "connected", "config": {"sample_rate": 16000, "chunk_size": 512}},
        {"type": "speech_start", "utterance_id": "test"},
        {"type": "processing"},
        {"type": "transcript", "text": "", "is_final": True},
        {"type": "transcript", "text": "Weight is 2.8 kg", "is_final": True},
    ])

    @asynccontextmanager
    async def connect(url, *, additional_headers, **kwargs):
        assert additional_headers["language_code"] == "hi-IN"
        yield socket

    monkeypatch.setattr(provider, "connect", connect)

    async def exercise():
        async with provider.create_voice("synthetic-key").listen("hi") as stream:
            await stream.send_pcm(b"\x12\x34" * 1600)
            await stream.send_pcm(b"\x56\x78" * 448)
            assert [event async for event in stream.transcripts()] == [("Weight is 2.8 kg", True)]

    asyncio.run(exercise())
    assert len(socket.sent) == 4
    assert all(isinstance(frame, bytes) and len(frame) == 1024 for frame in socket.sent)
    assert b"".join(socket.sent) == b"\x12\x34" * 1600 + b"\x56\x78" * 448


def test_timbre_streams_raw_pcm_until_complete(monkeypatch):
    pcm = b"\x12\x34"
    socket = MockSocket([
        {"type": "start", "request_id": "synthetic"},
        {"type": "audio", "data": {"audio": base64.b64encode(pcm).decode(), "is_final": False}},
        {"type": "audio", "data": {"audio": "AAA=", "is_final": False}},
        {"type": "complete", "data": {"audio": "", "is_final": True}},
        {"type": "error"},
    ])

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield socket

    monkeypatch.setattr(provider, "connect", connect)

    async def exercise():
        voice = provider.create_voice("synthetic-key")
        assert [chunk async for chunk in voice.speak("Synthetic test", "kn")] == [pcm, bytes(2)]

    asyncio.run(exercise())
    assert [json.loads(value) for value in socket.sent] == [{
        "text": "Synthetic test", "voice": "Shivani", "model": "timbre-v3.0",
        "language": "kn-IN", "speed": 1.0,
        "audio_config": {"sample_rate": 16000, "num_channels": 1, "sample_width": 2,
                         "encoding": "linear_pcm", "container": "raw"},
    }]


@pytest.mark.parametrize("message", [{"type": "error", "message": "private"}, {"type": "audio", "data": {"audio": "!"}}])
def test_timbre_rejects_error_or_invalid_audio(monkeypatch, message):
    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield MockSocket([message])

    monkeypatch.setattr(provider, "connect", connect)

    async def exercise():
        with pytest.raises(provider.GnaniProtocolError, match="Gnani Timbre"):
            await anext(provider.create_voice("synthetic-key").speak("Test", "en"))

    asyncio.run(exercise())


def test_default_provider_loads_without_connecting(monkeypatch):
    from backend.gnani.adapter import load_voice

    monkeypatch.setenv("GNANI_API_KEY", "synthetic-key")
    monkeypatch.setenv("GNANI_PROTOCOL_MODULE", "")
    assert isinstance(load_voice(), provider.GnaniVoice)


def test_timbre_cancellation_closes_active_synthesis(monkeypatch):
    async def exercise():
        waiting = asyncio.Event()
        closed = asyncio.Event()

        class StreamingSocket(MockSocket):
            async def __anext__(self):
                try:
                    return await super().__anext__()
                except StopAsyncIteration:
                    waiting.set()
                    await asyncio.Future()

        @asynccontextmanager
        async def connect(*args, **kwargs):
            try:
                yield StreamingSocket([{"type": "audio", "data": {"audio": "AAA="}}])
            finally:
                closed.set()

        monkeypatch.setattr(provider, "connect", connect)
        stream = provider.create_voice("synthetic-key").speak("Test", "en")
        assert await anext(stream) == bytes(2)
        task = asyncio.create_task(anext(stream))
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()

    asyncio.run(exercise())
