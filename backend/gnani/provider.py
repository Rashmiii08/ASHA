"""Authenticated transport for the confirmed Gnani WebSocket endpoints."""

import asyncio
import base64
import json
import logging
import math
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

LOG = logging.getLogger(__name__)
# WebSocket debug logging can include credential headers and voice frames.
WIRE_LOG = logging.Logger("gnani.websocket", logging.CRITICAL + 1)

PRISMA_URL = "wss://api.vachana.ai/stt/v3/stream"
TIMBRE_URL = "wss://api.vachana.ai/api/v1/tts"
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_SECONDS = FRAME_SAMPLES / 16000
VAD_THRESHOLD = "0.5"
MIN_SILENCE_MS = "700"


class GnaniConnectionError(ConnectionError):
    """A transport failure safe to report without exposing provider details."""


class GnaniTransport:
    """Open provider sockets using the account key supplied by the backend."""

    def __init__(self, api_key: str):
        if not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ValueError("A valid Gnani API key is required.")
        self._api_key = api_key

    def prisma(self, language="en"):
        return self._open("Prisma", PRISMA_URL, "x-api-key-id", {
            "language_code": _locale(language),
            "model": "prisma-v3.0",
            "x-sample-rate": "16000",
            "format": "transcribe",
            "x-vad-threshold": VAD_THRESHOLD,
            "x-min-silence-ms": MIN_SILENCE_MS,
        })

    def timbre(self):
        return self._open("Timbre", TIMBRE_URL, "X-API-Key-ID")

    @asynccontextmanager
    async def _open(self, service: str, url: str, header: str, settings=None) -> AsyncIterator[ClientConnection]:
        LOG.info("Gnani %s WebSocket connecting", service)
        try:
            async with connect(
                url,
                additional_headers={header: self._api_key, **(settings or {})},
                open_timeout=10,
                close_timeout=2,
                logger=WIRE_LOG,
            ) as socket:
                LOG.info("Gnani %s WebSocket upgrade accepted", service)
                yield socket
        except InvalidStatus as exc:
            status = exc.response.status_code
            LOG.warning("Gnani %s WebSocket upgrade rejected: HTTP %d", service, status)
            raise GnaniConnectionError(f"Gnani {service} WebSocket upgrade was rejected.") from None
        except (OSError, WebSocketException) as exc:
            LOG.warning("Gnani %s WebSocket failed: %s", service, type(exc).__name__)
            raise GnaniConnectionError(f"Gnani {service} WebSocket connection failed.") from None


class GnaniProtocolError(RuntimeError):
    """A malformed or unsuccessful provider response."""


def _json_message(raw, service):
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        raise GnaniProtocolError(f"Gnani {service} sent invalid JSON.") from None
    if not isinstance(message, dict):
        raise GnaniProtocolError(f"Gnani {service} sent an invalid message.")
    return message


def _locale(language: str) -> str:
    return f"{language}-IN" if "-" not in language else language


class PrismaStream:
    def __init__(self, socket: ClientConnection):
        self._socket = socket
        self._buffer = bytearray()
        self._next_frame_at = 0.0
        self._frames_sent = 0
        self._silent_frames = 0
        self._max_rms = 0.0
        self._peak = 0

    async def send_pcm(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            raise ValueError("Prisma requires complete PCM16 samples.")
        self._buffer.extend(pcm)
        loop = asyncio.get_running_loop()
        if self._next_frame_at < loop.time() - FRAME_SECONDS:
            self._next_frame_at = loop.time()
        while len(self._buffer) >= FRAME_BYTES:
            delay = self._next_frame_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            frame = bytes(self._buffer[:FRAME_BYTES])
            await self._socket.send(frame)
            del self._buffer[:FRAME_BYTES]
            self._frames_sent += 1
            if LOG.isEnabledFor(logging.INFO):
                self._record_level(frame)
            # Catch up after small scheduling delays without sending frames back-to-back.
            self._next_frame_at = max(self._next_frame_at + FRAME_SECONDS, loop.time() + 0.028)

    def _record_level(self, frame: bytes) -> None:
        peak = energy = 0
        for (sample,) in struct.iter_unpack("<h", frame):
            peak = max(peak, abs(sample))
            energy += sample * sample
        self._silent_frames += peak == 0
        self._max_rms = max(self._max_rms, math.sqrt(energy / FRAME_SAMPLES))
        self._peak = max(self._peak, peak)
        if self._frames_sent in (1, 32) or self._frames_sent % 300 == 0:
            LOG.info(
                "Prisma PCM frames sent: frames=%d bytes=%d silent_frames_window=%d "
                "max_rms_dbfs=%.1f peak_dbfs=%.1f",
                self._frames_sent, self._frames_sent * FRAME_BYTES, self._silent_frames,
                20 * math.log10(max(1, self._max_rms) / 32768),
                20 * math.log10(max(1, self._peak) / 32768),
            )
            self._silent_frames = self._max_rms = self._peak = 0

    async def finish_utterance(self) -> None:
        if self._buffer:
            await self.send_pcm(bytes(FRAME_BYTES - len(self._buffer)))
        await self._socket.send(json.dumps({"type": "commit"}))
        LOG.info("Prisma utterance commit sent")

    async def transcripts(self) -> AsyncIterator[tuple[str, bool]]:
        async for raw in self._socket:
            message = _json_message(raw, "Prisma")
            kind = message.get("type")
            if kind in ("speech_start", "processing", "transcript", "error"):
                LOG.info("Prisma event: %s", kind)
            if kind == "error":
                raise GnaniProtocolError("Gnani Prisma reported a transcription error.")
            if kind == "transcript":
                payload = message.get("text")
                if not isinstance(payload, str) or message.get("is_final", True) is not True:
                    raise GnaniProtocolError("Gnani Prisma sent an invalid final transcript.")
                if payload.strip():
                    yield payload, True
                else:
                    LOG.info("Prisma final transcript was empty")


class GnaniVoice(GnaniTransport):
    @asynccontextmanager
    async def listen(self, language: str) -> AsyncIterator[PrismaStream]:
        async with self.prisma(language) as socket:
            raw = await asyncio.wait_for(socket.recv(), timeout=10)
            message = _json_message(raw, "Prisma")
            if message.get("type") != "connected":
                raise GnaniProtocolError("Gnani Prisma did not acknowledge the stream.")
            config = message.get("config")
            if not isinstance(config, dict) or config.get("sample_rate") != 16000 or config.get("chunk_size") != 512:
                raise GnaniProtocolError("Gnani Prisma negotiated an unexpected audio format.")
            LOG.info("Prisma stream ready: sample_rate=16000 frame_samples=512 vad_threshold=%s min_silence_ms=%s",
                     VAD_THRESHOLD, MIN_SILENCE_MS)
            yield PrismaStream(socket)

    async def speak(self, text: str, language: str) -> AsyncIterator[bytes]:
        request = {
            "text": text,
            "voice": "Shivani",
            "model": "timbre-v3.0",
            "language": _locale(language),
            "speed": 1.0,
            "audio_config": {
                "sample_rate": 16000, "num_channels": 1, "sample_width": 2,
                "encoding": "linear_pcm", "container": "raw",
            },
        }
        async with self.timbre() as socket:
            await socket.send(json.dumps(request, ensure_ascii=False))
            async for raw in socket:
                message = _json_message(raw, "Timbre")
                match message.get("type"):
                    case "start":
                        continue
                    case "audio":
                        try:
                            chunk = base64.b64decode(message["data"]["audio"], validate=True)
                        except (KeyError, TypeError, ValueError):
                            raise GnaniProtocolError("Gnani Timbre sent invalid audio.") from None
                        if not chunk or len(chunk) % 2:
                            raise GnaniProtocolError("Gnani Timbre sent invalid PCM16.")
                        yield chunk
                    case "complete":
                        return
                    case "error":
                        raise GnaniProtocolError("Gnani Timbre rejected the synthesis request.")
            raise GnaniProtocolError("Gnani Timbre disconnected before completing synthesis.")


def create_voice(api_key: str) -> GnaniVoice:
    return GnaniVoice(api_key)
