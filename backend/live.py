import asyncio
import base64
import binascii
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .db import Register, delete_visits, demo_key
from .dialogue import Dialogue
from .gnani.adapter import load_voice
from .llm import configured_gemini
from .schemas import Language

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)
MAX_SESSION_IDLE = 1800


class Hello(BaseModel):
    type: str
    language: Language = "en"
    session: UUID | None = None


@dataclass
class Session:
    dialogue: Dialogue
    touched: float = field(default_factory=time.monotonic)
    connected: bool = False


def same_origin(scope):
    origin = scope.headers.get("origin")
    return origin is None or origin in {
        f"http://{scope.headers.get('host')}", f"https://{scope.headers.get('host')}",
    }


def create_live_app():
    load_dotenv(ROOT / ".env")
    mode = os.getenv("APP_MODE", "demo")
    if mode not in {"demo", "real"}:
        raise ValueError("APP_MODE must be demo or real")
    sessions = {}

    @asynccontextmanager
    async def lifespan(app):
        path = ROOT / os.getenv("DATABASE_PATH", "data/visits.sqlite3")
        key = os.getenv("DATA_KEY")
        if mode == "real" and not key:
            raise ValueError("Set a persistent Fernet DATA_KEY for real mode.")
        app.state.register = await run_in_threadpool(
            lambda: Register(path, key.encode() if key else demo_key(path.with_suffix(".key")))
        )
        app.state.voice = None
        app.state.voice_error = None
        app.state.gemini = configured_gemini() if mode == "real" else None
        if mode == "real":
            try:
                app.state.voice = load_voice()
            except Exception:
                app.state.voice_error = "Gnani setup is incomplete. Check the API key and verified protocol module."
        try:
            yield
        finally:
            sessions.clear()
            if app.state.gemini:
                await app.state.gemini.close()

    app = FastAPI(title="Voice Field Register", lifespan=lifespan)

    @app.get("/api/status")
    async def status():
        return {"mode": mode, "voice_ready": bool(app.state.voice), "voice_error": app.state.voice_error,
                "gemini_configured": bool(app.state.gemini)}

    @app.get("/api/visits")
    async def visits():
        return await run_in_threadpool(app.state.register.list)

    @app.patch("/api/visits/{visit_id}/followup")
    async def complete(visit_id: UUID, request: Request):
        if not same_origin(request):
            raise HTTPException(403)
        if not await run_in_threadpool(app.state.register.complete_followup, str(visit_id)):
            raise HTTPException(404, "Follow-up not found")
        return {"completed": True}

    @app.delete("/api/visits")
    async def clear(request: Request):
        if not same_origin(request) or request.headers.get("x-confirm-delete") != "synthetic-visits":
            raise HTTPException(403)
        def apply():
            with app.state.register.connection() as db:
                delete_visits(db)
        await run_in_threadpool(apply)
        return {"deleted": True}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket):
        if not same_origin(socket):
            await socket.close(code=1008)
            return
        await socket.accept()
        session = None
        announced = False
        tasks = set()
        playback = None
        generation = 0
        send_lock = asyncio.Lock()
        playback_lock = asyncio.Lock()

        async def send(payload):
            async with send_lock:
                await socket.send_json(payload)

        async def error(message, fatal=False):
            try:
                await send({"type": "error", "message": message, "fatal": fatal})
            except WebSocketDisconnect:
                pass

        async def stop_playback():
            nonlocal generation, playback
            generation += 1
            if playback:
                playback.cancel()
                await asyncio.gather(playback, return_exceptions=True)
                playback = None

        async def update(latency=None):
            await send({
                "type": "update", "state": dialogue.state,
                "form": dialogue.visit.model_dump(mode="json"),
                "danger_flag": bool(dialogue.danger), "latency_ms": latency,
            })

        async def play(text, processing_ms=None):
            nonlocal playback

            async def speak(current_generation):
                first = True
                try:
                    async with asyncio.timeout(45):
                        async for chunk in app.state.voice.speak(text, dialogue.language):
                            if not chunk or current_generation != generation:
                                continue
                            await send({"type": "audio", "data": base64.b64encode(chunk).decode(),
                                        "is_first_chunk": first, "generation": current_generation})
                            first = False
                    await send({"type": "audio_end", "generation": current_generation})
                except (WebSocketDisconnect, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    LOG.warning("Speech synthesis failed (%s)", type(exc).__name__)
                    await error("Gnani speech playback failed.")

            async with playback_lock:
                await stop_playback()
                current_generation = generation
                await send({"type": "assistant", "text": text, "generation": current_generation,
                            "processing_ms": processing_ms})
                if app.state.voice is not None:
                    playback = asyncio.create_task(speak(current_generation))

        async def handle_text(text):
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 3000:
                await error("A transcript must contain 1 to 3000 characters.")
                return
            await send({"type": "transcript", "text": text, "final": True})
            await send({"type": "phase", "state": "PROCESSING"})
            started = time.monotonic()
            extract = app.state.gemini.extract if app.state.gemini else None
            try:
                async with asyncio.timeout(20):
                    reply = await dialogue.process(text, extract, store)
            except Exception as exc:
                LOG.warning("Visit processing failed (%s)", type(exc).__name__)
                await error("Could not process the visit. Repeat the last instruction.")
                await update()
                return
            await update()
            await play(reply, round((time.monotonic() - started) * 1000))

        async def store(draft):
            await run_in_threadpool(app.state.register.insert, draft)

        async def receive(stream=None):
            async for raw in socket.iter_text():
                try:
                    if len(raw) > 25_000:
                        raise ValueError
                    message = json.loads(raw)
                    kind = message["type"]
                except (ValueError, TypeError, KeyError):
                    await error("Invalid WebSocket message.")
                    continue
                if kind == "interrupt":
                    async with playback_lock:
                        await stop_playback()
                        await send({"type": "interrupted", "generation": generation})
                elif kind == "heartbeat":
                    await send({"type": "heartbeat"})
                elif kind == "audio":
                    try:
                        pcm = base64.b64decode(message["data"], validate=True)
                        if not pcm or len(pcm) % 2 or len(pcm) > 16_000:
                            raise ValueError
                    except (KeyError, ValueError, TypeError, binascii.Error):
                        await error("Expected a bounded base64 PCM16 audio chunk.")
                        continue
                    if stream:
                        await stream.send_pcm(pcm)
                elif kind == "demo_transcript" and mode == "demo":
                    if pending.full():
                        await error("Wait for the current turn to finish.")
                    else:
                        pending.put_nowait(message.get("text"))
                else:
                    await error("Unsupported message type.")

        async def transcripts(stream):
            async for text, final in stream.transcripts():
                if final:
                    if pending.full():
                        await error("Wait for the current turn to finish.")
                    else:
                        pending.put_nowait(text)
                else:
                    await send({"type": "transcript", "text": text, "final": False})
            raise RuntimeError("Speech stream ended")

        async def turns():
            while True:
                await handle_text(await pending.get())

        async def run(stream=None):
            tasks.update((asyncio.create_task(receive(stream)), asyncio.create_task(turns())))
            if stream:
                tasks.add(asyncio.create_task(transcripts(stream)))
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                tasks.clear()

        async def announce():
            nonlocal announced
            await send({"type": "ready", "session": token, "mode": mode, "resumed": existing is not None})
            announced = True
            await update()

        pending = asyncio.Queue(maxsize=1)
        try:
            raw_hello = await asyncio.wait_for(socket.receive_text(), timeout=10)
            if len(raw_hello) > 1000:
                raise ValueError("Oversized handshake")
            hello = Hello.model_validate_json(raw_hello)
            if hello.type != "hello":
                await error("A hello message is required.", fatal=True)
                return
            if app.state.voice_error:
                await error(app.state.voice_error, fatal=True)
                return
            now = time.monotonic()
            for token, item in list(sessions.items()):
                if not item.connected and now - item.touched > MAX_SESSION_IDLE:
                    del sessions[token]
            token = str(hello.session) if hello.session else str(uuid4())
            existing = sessions.get(token)
            if existing and existing.connected:
                await error("The session is reconnecting.", fatal=False)
                return
            if hello.session and not existing:
                await error("The server session expired. Start a new visit.", fatal=True)
                return
            if existing is None and len(sessions) >= 128:
                await error("The register is at capacity. Try again shortly.", fatal=True)
                return
            session = existing or Session(Dialogue(hello.language))
            sessions[token] = session
            session.connected = True
            dialogue = session.dialogue
            if mode == "demo":
                await announce()
                if not existing:
                    await play(dialogue.phrases["disclose"])
                await run()
            else:
                async with app.state.voice.listen(dialogue.language) as stream:
                    await announce()
                    if not existing:
                        await play(dialogue.phrases["disclose"])
                    await run(stream)
        except WebSocketDisconnect:
            pass
        except (ValueError, asyncio.TimeoutError):
            await error("The session could not be started.", fatal=True)
        except Exception as exc:
            LOG.warning("Voice session failed (%s)", type(exc).__name__)
            await error("The voice connection ended. Reconnecting may restore your draft.")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await stop_playback()
            if session:
                session.connected = False
                session.touched = time.monotonic()
                if existing is None and not announced:
                    del sessions[token]
            try:
                await socket.close()
            except (RuntimeError, WebSocketDisconnect):
                pass

    app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app
