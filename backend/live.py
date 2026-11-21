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

from anyio import CancelScope
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from starlette import websockets as starlette_ws
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketState

from .db import Register, delete_visits, demo_key
from .dialogue import Dialogue
from .gnani.adapter import load_voice
from .gnani.provider import GnaniProtocolError, GnaniUpgradeError, PrismaStream
from .llm import configured_gemini
from .schemas import Language, VisitLocationCapture, VisitReview
from .terminology import TERMS

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)
MAX_SESSION_IDLE = 1800
BROWSER_DISCONNECTS = (WebSocketDisconnect, getattr(starlette_ws, "WebSocketDisconnected", WebSocketDisconnect))


class Hello(BaseModel):
    type: str
    language: Language = "en"
    session: UUID | None = None
    review_only: bool = False


@dataclass
class Session:
    dialogue: Dialogue
    touched: float = field(default_factory=time.monotonic)
    connected: bool = False
    last_final: str | None = None
    final_sequence: int = 0
    displayed_final_sequence: int = 0
    revision: int = 0


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
                app.state.voice_error = "Gnani setup is incomplete. Check GNANI_API_KEY and any provider override."
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
    async def visits(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        records = await run_in_threadpool(app.state.register.list)
        if request.url.scheme != "https":
            for record in records:
                record.pop("location", None)
        return records

    @app.patch("/api/visits/{visit_id}/followup")
    async def complete(visit_id: UUID, request: Request, followup_id: str = "visit"):
        if not same_origin(request):
            raise HTTPException(403)
        if not await run_in_threadpool(app.state.register.complete_followup, str(visit_id), followup_id):
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
        secure_location_transport = socket.url.scheme == "wss"
        session = None
        announced = False
        tasks = set()
        playback = None
        generation = 0
        send_lock = asyncio.Lock()
        playback_lock = asyncio.Lock()
        browser_gone = False
        audio_chunks = audio_bytes = 0
        capture_paused = False
        processing_turn = False
        final_delivery_pending = False
        turn_lock = asyncio.Lock()

        def browser_connected():
            return (
                not browser_gone
                and socket.client_state is WebSocketState.CONNECTED
                and socket.application_state is WebSocketState.CONNECTED
            )

        async def send(payload):
            nonlocal browser_gone
            async with send_lock:
                if not browser_connected():
                    raise WebSocketDisconnect()
                try:
                    await socket.send_json(payload)
                except BROWSER_DISCONNECTS:
                    browser_gone = True
                    raise

        async def error(message, fatal=False):
            try:
                await send({"type": "error", "message": message, "fatal": fatal})
            except BROWSER_DISCONNECTS:
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
                "form": {**dialogue.visit.model_dump(mode="json"), "reported_conditions": dialogue.condition_details()},
                "revision": session.revision,
                "location": dialogue.location.model_dump(mode="json") if secure_location_transport and dialogue.location else None,
                "review": {
                    "concern_catalog": list(TERMS),
                    "pending_diagnoses": dialogue.pending_diagnoses(),
                    "needs_clarification": dialogue.needs_clarification,
                    "blockers": dialogue.save_blockers(),
                    "transcript_correction": dialogue.transcript_correction,
                },
                "danger_flag": bool(dialogue.danger), "latency_ms": latency,
            })

        async def play(text, processing_ms=None):
            nonlocal playback

            async def speak(current_generation):
                first = True
                try:
                    async with asyncio.timeout(45):
                        stream = app.state.voice.speak(text, dialogue.language)
                        try:
                            async for chunk in stream:
                                if not chunk or current_generation != generation:
                                    continue
                                await send({"type": "audio", "data": base64.b64encode(chunk).decode(),
                                            "is_first_chunk": first, "generation": current_generation})
                                first = False
                        finally:
                            close = getattr(stream, "aclose", None)
                            if close is not None:
                                await close()
                    await send({"type": "audio_end", "generation": current_generation})
                except BROWSER_DISCONNECTS:
                    return
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

        async def handle_text(text, *, announced=False):
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 3000:
                await error("A transcript must contain 1 to 3000 characters.")
                return
            if not announced:
                await send({"type": "transcript", "text": text, "final": True})
            await send({"type": "phase", "state": "PROCESSING"})
            started = time.monotonic()
            extract = app.state.gemini.extract if app.state.gemini else None
            try:
                async with asyncio.timeout(20):
                    reply = await dialogue.process(text, extract, store, allow_save=not capture_paused)
            except Exception as exc:
                LOG.warning("Visit processing failed (%s)", type(exc).__name__)
                session.revision += 1
                await error("Could not process the visit. Repeat the last instruction.")
                await update()
                return
            session.revision += 1
            await update()
            if capture_paused:
                generation_at_reply = generation
                await send({"type": "assistant", "text": reply, "generation": generation_at_reply})
            else:
                await play(reply, round((time.monotonic() - started) * 1000))

        async def store(draft):
            await run_in_threadpool(app.state.register.insert, draft)

        async def receive(stream=None):
            nonlocal browser_gone, audio_chunks, audio_bytes, capture_paused
            while True:
                try:
                    raw = await socket.receive_text()
                except BROWSER_DISCONNECTS:
                    browser_gone = True
                    raise
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
                elif kind == "pause_capture":
                    capture_paused = True
                    async with playback_lock:
                        await stop_playback()
                    finish = getattr(stream, "finish_utterance", None)
                    if finish is not None:
                        await finish()
                    await send({"type": "capture_paused"})
                elif kind == "set_visit_location":
                    if not secure_location_transport:
                        await send({"type": "location_error", "message": "Location capture requires HTTPS."})
                        continue
                    if dialogue.state == "SAVED":
                        await send({"type": "location_error", "message": "This visit has already been saved."})
                        continue
                    try:
                        capture = VisitLocationCapture.model_validate(message)
                    except ValidationError:
                        await send({"type": "location_error", "message": "The GPS reading was invalid. Try refreshing the location."})
                        continue
                    dialogue.location = capture.location
                    await send({"type": "visit_location", "request_id": capture.request_id,
                                "location": capture.location.model_dump(mode="json")})
                elif kind in ("edit_visit", "confirm_visit"):
                    if dialogue.state == "SAVED":
                        await update()
                        continue
                    if mode == "real" and not capture_paused:
                        await error("Stop recording before reviewing or saving the visit.")
                        continue
                    if processing_turn or final_delivery_pending or not pending.empty() or message.get("revision") != session.revision:
                        await error("The draft is still updating. Review the latest details and try again.")
                        continue
                    async with turn_lock:
                        if kind == "edit_visit" or "retained_conditions" in message:
                            try:
                                dialogue.apply_review(VisitReview.model_validate(message))
                            except ValidationError as exc:
                                problems = exc.errors(include_input=False)
                                if any(item["loc"] == ("beneficiary_id",) for item in problems):
                                    await error("The beneficiary ID must contain exactly four numeric digits.")
                                elif any("unusually high for a newborn" in item["msg"] for item in problems):
                                    await error("This weight is unusually high for a newborn. Confirm the weight in kilograms.")
                                else:
                                    await error("Check the visit type, measurements, vaccination schedule, and clarification answers.")
                                continue
                            except ValueError as exc:
                                if str(exc) == "Follow-up dates must be today or later.":
                                    await error("Follow-up dates must be today or later.")
                                else:
                                    await error("Check the reported concerns and clarification answers.")
                                continue
                            session.revision += 1
                        if kind == "edit_visit":
                            await update()
                            continue
                        blockers = dialogue.save_blockers()
                        if blockers or dialogue.state != "CONFIRMING":
                            await update()
                            await error(" ".join(blockers) or "Review the draft before saving.")
                            continue
                        try:
                            await dialogue.confirm(store)
                        except Exception as exc:
                            LOG.warning("Visit save failed (%s)", type(exc).__name__)
                            await update()
                            await error("Could not save the visit. The draft is still available. Please try again.")
                        else:
                            session.revision += 1
                            await update()
                elif kind == "finish_utterance" and stream:
                    finish = getattr(stream, "finish_utterance", None)
                    if finish is None:
                        await error("This voice provider does not support manual utterance completion.")
                    else:
                        await finish()
                elif kind == "transcript_displayed" and stream:
                    sequence = message.get("sequence")
                    if type(sequence) is int and session.displayed_final_sequence < sequence <= session.final_sequence:
                        session.displayed_final_sequence = sequence
                        LOG.info("Browser acknowledged a nonempty final transcript")
                elif kind == "heartbeat":
                    response = {"type": "heartbeat"}
                    if stream:
                        response["audio_chunks"] = audio_chunks
                        response["final_sequence"] = session.final_sequence
                        response["displayed_final_sequence"] = session.displayed_final_sequence
                        if isinstance(stream, PrismaStream):
                            response["prisma"] = stream.diagnostics()
                    await send(response)
                elif kind == "audio":
                    try:
                        pcm = base64.b64decode(message["data"], validate=True)
                        if not pcm or len(pcm) % 2 or len(pcm) > 16_000:
                            raise ValueError
                    except (KeyError, ValueError, TypeError, binascii.Error):
                        await error("Expected a bounded base64 PCM16 audio chunk.")
                        continue
                    if stream and not capture_paused:
                        audio_chunks += 1
                        audio_bytes += len(pcm)
                        if audio_chunks == 1 or audio_chunks % 100 == 0:
                            LOG.info("Browser microphone PCM received: chunks=%d bytes=%d", audio_chunks, audio_bytes)
                        await stream.send_pcm(pcm)
                elif kind == "demo_transcript" and mode == "demo" and not capture_paused:
                    if pending.full():
                        await error("Wait for the current turn to finish.")
                    else:
                        pending.put_nowait(message.get("text"))
                else:
                    await error("Unsupported message type.")

        async def prisma_notice(code):
            await send({"type": "prisma_notice", "code": code})

        async def transcripts(stream):
            nonlocal final_delivery_pending
            events = stream.transcripts(prisma_notice) if isinstance(stream, PrismaStream) else stream.transcripts()
            async for text, final in events:
                if not isinstance(text, str) or not text.strip():
                    continue
                text = text.strip()
                payload = {"type": "transcript", "text": text[:3000], "final": final}
                if final:
                    # Retain the newest final before attempting a write that can race disconnect.
                    session.last_final = payload["text"]
                    session.final_sequence += 1
                    payload["sequence"] = session.final_sequence
                final_delivery_pending = final
                try:
                    await send(payload)
                finally:
                    final_delivery_pending = False
                if len(text) > 3000:
                    if final:
                        await error("The transcript is too long to process. Please speak a shorter note.")
                    continue
                if final:
                    if pending.full():
                        await error("Wait for the current turn to finish.")
                    else:
                        pending.put_nowait(text)

            raise RuntimeError("Speech stream ended")

        async def turns():
            nonlocal processing_turn
            while True:
                text = await pending.get()
                processing_turn = True
                try:
                    async with turn_lock:
                        await handle_text(text, announced=mode == "real")
                finally:
                    processing_turn = False

        async def cleanup():
            # Finish joining the child tasks even if the ASGI session is cancelled.
            with CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                tasks.clear()
                await stop_playback()

        async def run(stream=None):
            tasks.update((asyncio.create_task(receive(stream)), asyncio.create_task(turns())))
            if stream:
                tasks.add(asyncio.create_task(transcripts(stream)))
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if not browser_connected():
                    return
                for task in done:
                    task.result()
            finally:
                await cleanup()

        async def announce():
            nonlocal announced
            await send({"type": "ready", "session": token, "mode": mode, "resumed": existing is not None})
            announced = True
            await update()
            if existing and session.last_final is not None:
                await send({"type": "transcript", "text": session.last_final, "final": True,
                            "sequence": session.final_sequence})

        pending = asyncio.Queue(maxsize=8 if mode == "real" else 1)
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
            capture_paused = hello.review_only and existing is not None
            if mode == "demo" or capture_paused:
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
        except BROWSER_DISCONNECTS:
            browser_gone = True
        except GnaniUpgradeError as exc:
            await error(f"Prisma connection rejected (HTTP {exc.status}).", fatal=exc.status in (401, 403))
        except GnaniProtocolError:
            message = ("Prisma returned an unreadable response. Reconnecting."
                       if announced else "Prisma did not accept the audio stream setup. Reconnecting.")
            await error(message)
        except (ValueError, asyncio.TimeoutError):
            await error("The session could not be started.", fatal=True)
        except Exception as exc:
            if browser_connected():
                LOG.warning("Voice session failed (%s)", type(exc).__name__)
                await error("The voice connection ended. Reconnecting may restore your draft.")
        finally:
            await cleanup()
            if session:
                session.connected = False
                session.touched = time.monotonic()
                if existing is None and not announced:
                    del sessions[token]
            if mode == "real" and announced:
                LOG.info("Voice session ended: browser_pcm_chunks=%d browser_pcm_bytes=%d", audio_chunks, audio_bytes)
            if browser_connected():
                try:
                    await socket.close()
                except BROWSER_DISCONNECTS:
                    pass

    app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
    return app
