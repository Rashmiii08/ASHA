"""Short push-to-talk Gnani conversation, independent of the streaming STT path."""
import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .dialogue import Dialogue
from .gnani.provider import create_voice

LOG = logging.getLogger(__name__)
router = APIRouter()
_sessions = {}
MAX_AUDIO_BYTES = 1_000_000
MAX_AUDIO_SECONDS = 30


@dataclass
class TurnSession:
    dialogue: Dialogue
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TurnStart(BaseModel):
    language: str = "en"


class TurnConfirm(BaseModel):
    session: str


def _session(token: str) -> TurnSession:
    session = _sessions.get(token)
    if session is None or time.monotonic() - session.touched > 1800:
        raise HTTPException(404, "Voice session expired. Start again.")
    return session


def _result(session, text, reply, audio):
    d = session.dialogue
    return {"transcript": text, "assistant": reply, "audio": base64.b64encode(audio).decode(),
            "state": d.state, "form": d.visit.model_dump(mode="json"),
            "danger_flag": bool(d.danger)}


async def _speech(reply, language):
    voice = create_voice(os.environ["GNANI_API_KEY"])
    audio = bytearray()
    try:
        async with asyncio.timeout(30):
            async for chunk in voice.speak(reply, language):
                audio.extend(chunk)
                if len(audio) > 1_600_000:
                    break
    except Exception as exc:
        LOG.warning("Turn voice playback failed (%s)", type(exc).__name__)
    return bytes(audio)


async def _process(session, text, request):
    from .llm import configured_gemini
    from starlette.concurrency import run_in_threadpool
    extract_client = configured_gemini()
    try:
        async def store(dialogue):
            await run_in_threadpool(request.app.state.register.insert, dialogue)
        extract = extract_client.extract if extract_client else None
        reply = await session.dialogue.process(text, extract, store)
    finally:
        if extract_client:
            await extract_client.close()
    session.touched = time.monotonic()
    return _result(session, text, reply, await _speech(reply, session.dialogue.language))


@router.post("/api/voice-turn/start")
async def start_turn(payload: TurnStart, request: Request):
    if payload.language not in ("en", "hi"):
        raise HTTPException(400, "Select English or Hindi.")
    if not os.getenv("GNANI_API_KEY"):
        raise HTTPException(503, "Gnani API key is not configured.")
    for key, session in list(_sessions.items()):
        if time.monotonic() - session.touched > 1800:
            del _sessions[key]
    if len(_sessions) >= 128:
        raise HTTPException(503, "Too many active voice sessions.")
    token = str(uuid4())
    session = TurnSession(Dialogue(payload.language))
    _sessions[token] = session
    reply = session.dialogue.phrases["disclose"]
    result = _result(session, "", reply, await _speech(reply, payload.language))
    result["session"] = token
    return result


@router.post("/api/voice-turn/{token}")
async def voice_turn(token: str, request: Request):
    session = _session(token)
    if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
        raise HTTPException(415, "Expected WAV microphone audio.")
    audio = await request.body()
    if len(audio) > MAX_AUDIO_BYTES or len(audio) < 44 or not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
        raise HTTPException(400, "Record a short voice message (up to 30 seconds).")
    async with session.lock:
        key = os.getenv("GNANI_API_KEY")
        if not key:
            raise HTTPException(503, "Gnani API key is not configured.")
        try:
            async with httpx.AsyncClient(timeout=40) as client:
                response = await client.post(
                    "https://api.vachana.ai/stt/v3",
                    headers={"X-API-Key-ID": key},
                    files={"audio_file": ("speech.wav", audio, "audio/wav")},
                    data={"model": "prisma-v3.0", "language_code": session.dialogue.language + "-IN",
                          "format": "transcribe"},
                )
            if response.status_code != 200:
                LOG.warning("Gnani REST transcription HTTP %s", response.status_code)
                raise HTTPException(502, "Gnani could not recognize that recording. Try again.")
            body = response.json()
            transcript = body.get("transcript")
            if not isinstance(transcript, str) or not transcript.strip():
                raise HTTPException(422, "No speech recognized. Speak clearly and try again.")
            return await _process(session, transcript[:3000], request)
        except httpx.HTTPError:
            raise HTTPException(502, "Could not reach Gnani. Please try again.") from None


@router.post("/api/voice-turn/{token}/confirm")
async def confirm_turn(token: str, request: Request):
    session = _session(token)
    async with session.lock:
        return await _process(session, "Yes, save it." if session.dialogue.language == "en" else "हाँ सेव करें", request)
