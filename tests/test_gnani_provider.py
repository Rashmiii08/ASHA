import asyncio
import base64
import json
import logging
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
    async def connect(target, *, additional_headers, open_timeout, close_timeout, logger):
        assert target == url
        assert additional_headers[header] == "synthetic-test-key"
        if service == "prisma":
            assert additional_headers == {
                header: "synthetic-test-key", "lang_code": "en-IN", "model": "prisma-v3.0",
                "x-sample-rate": "16000", "x-format": "transcribe",
                "x-vad-threshold": "0.5", "x-min-silence-ms": "700",
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


def test_prisma_acknowledgement_binary_frames_and_final_transcript(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=provider.__name__)
    socket = MockSocket([
        {"type": "connected", "config": {"sample_rate": 16000, "chunk_size": 512}},
        {"type": "speech_start", "utterance_id": "test"},
        {"type": "processing"},
        {"type": "transcript", "text": "", "is_final": True},
        {"type": "transcript", "text": "Weight is 2.8 kg", "is_final": True},
    ])

    @asynccontextmanager
    async def connect(url, *, additional_headers, **kwargs):
        assert additional_headers["lang_code"] == "hi-IN"
        yield socket

    monkeypatch.setattr(provider, "connect", connect)

    async def exercise():
        async with provider.create_voice("synthetic-key").listen("hi") as stream:
            await stream.send_pcm(b"\x12\x34" * 1600)
            await stream.send_pcm(b"\x56\x78" * 448)
            assert [event async for event in stream.transcripts()] == [("Weight is 2.8 kg", True)]

    asyncio.run(exercise())
    assert "Prisma stream ready" in caplog.text
    assert "Prisma PCM frames sent: frames=1 bytes=1024" in caplog.text
    assert "Prisma event: transcript" in caplog.text
    assert "Weight is 2.8 kg" not in caplog.text
    assert "synthetic-key" not in caplog.text
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


def test_prisma_commit_flushes_remaining_samples_and_logs_levels(caplog):
    caplog.set_level(logging.INFO, logger=provider.__name__)
    socket = MockSocket([])
    speech = b"\x00\x40" * 512

    async def exercise():
        stream = provider.PrismaStream(socket)
        await stream.send_pcm(speech + b"\x00\x40" * 3)
        await stream.finish_utterance()

    asyncio.run(exercise())
    assert socket.sent == [speech, b"\x00\x40" * 3 + bytes(1018), '{"type": "commit"}']
    assert "silent_frames_window=0 max_rms_dbfs=-6.0 peak_dbfs=-6.0" in caplog.text
    assert "Prisma utterance commit sent" in caplog.text


def test_prisma_paces_binary_frames_from_legacy_batches():
    sent_at = []

    class TimedSocket(MockSocket):
        async def send(self, data):
            sent_at.append(asyncio.get_running_loop().time())
            await super().send(data)

    socket = TimedSocket([])
    asyncio.run(provider.PrismaStream(socket).send_pcm(bytes(3072)))
    assert socket.sent == [bytes(1024)] * 3
    assert all(later - earlier >= 0.020 for earlier, later in zip(sent_at, sent_at[1:]))


def test_prisma_partial_and_recoverable_error_do_not_close_stream():
    socket = MockSocket([
        {"type": "speech_start"},
        {"type": "transcript", "text": "Synthetic partial", "is_final": False},
        {"type": "error", "message": "Sensitive provider detail"},
        {"type": "processing"},
        {"type": "transcript", "text": "Synthetic final", "is_final": True},
        {"type": "transcript", "text": ""},
    ])

    async def exercise():
        stream = provider.PrismaStream(socket)
        await stream.send_pcm(bytes(1024))
        await stream.send_pcm(b"\x00\x20" * 512)
        assert [item async for item in stream.transcripts()] == [
            ("Synthetic partial", False), ("Synthetic final", True),
        ]
        assert stream.diagnostics() == {
            "frames": 2, "nonzero_frames": 1, "peak_dbfs": -12.0, "speech_starts": 1,
            "processing": 1, "partials": 1, "finals": 1, "empty": 1, "errors": 1, "other": 0, "commits": 0,
        }

    asyncio.run(exercise())
