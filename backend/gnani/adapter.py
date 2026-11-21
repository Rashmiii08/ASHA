"""Boundary for the account's verified Prisma and Timbre WebSocket protocol."""

import importlib
import os
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Protocol


class SpeechStream(Protocol):
    async def send_pcm(self, pcm: bytes) -> None: ...
    def transcripts(self) -> AsyncIterator[tuple[str, bool]]: ...


class Voice(Protocol):
    def listen(self, language: str) -> AbstractAsyncContextManager[SpeechStream]: ...
    def speak(self, text: str, language: str) -> AsyncIterator[bytes]: ...


def load_voice() -> Voice:
    key = os.getenv("GNANI_API_KEY")
    module = os.getenv("GNANI_PROTOCOL_MODULE")
    if not key or not module:
        raise ValueError("Real mode requires a Gnani API key and a verified Gnani protocol module.")
    factory = importlib.import_module(module).create_voice
    voice = factory(key)
    if not callable(getattr(voice, "listen", None)) or not callable(getattr(voice, "speak", None)):
        raise ValueError("The Gnani protocol module does not implement the voice interface.")
    return voice
