# API setup

Use synthetic visit notes when sending data to external providers. Keep keys in Codex environment secrets or the ignored local `.env` file.

## Configure Gnani in Codex

1. Open **ASHA environment settings**. Add `api.vachana.ai` to the allowed Internet hostnames if the environment restricts outbound access.
2. Under **Secrets**, add `GNANI_API_KEY` and enter your key privately. If Codex asks for an allowed destination, use `api.vachana.ai`. If secrets settings are unavailable, copy `.env.example` to `.env` in the workspace and set the value in the editor.
3. Set a persistent `DATA_KEY` in your ignored `.env`. Leave `GNANI_PROTOCOL_MODULE` blank to use the bundled provider. `APP_MODE=demo` is the default. To run a controlled live smoke test, set `APP_MODE=real` and restart the server.
4. Open a voice visit in English or Hindi. Speak a synthetic note, verify the final transcript and spoken reply, and interrupt a reply. Verify reconnect, correction and confirmation before relying on live mode.

## WebSocket protocol

| Service | Model | Endpoint | API-key header |
| --- | --- | --- | --- |
| Prisma | `prisma-v3.0` | `wss://api.vachana.ai/stt/v3/stream` | `x-api-key-id` |
| Timbre | `timbre-v3.0` | `wss://api.vachana.ai/api/v1/tts` | `X-API-Key-ID` |

Prisma is configured through upgrade headers and acknowledges with `connected`. The browser renders mono, little-endian, signed PCM16 at 16 kHz in 1,024-byte (32 ms) frames. The adapter paces the binary WebSocket frames, keeps silence flowing between words, uses the documented web-microphone VAD threshold of 0.5 with 700 ms of trailing silence, and submits completed `transcript` segments to the ASHA dialogue. The supplied Prisma PDF was truncated after page 19 of 34. The recovered pages and published SDK document completed segments. The app can display a transcript frame marked `is_final: false` separately from final text. The recovered reference does not define a distinct partial event or confirm that this endpoint emits partials.

Timbre uses the documented v3.0 voice Shivani and requests raw 16 kHz mono PCM16. The adapter streams base64-decoded `audio` messages and stops on `complete`, cancellation or error. The existing ASHA language picker offers English and Hindi. Provider request construction for Kannada is covered by a mock test.

[Prisma WebSocket reference](https://docs.gnani.ai/api/STT/stt-websocket) · [Timbre WebSocket reference](https://docs.gnani.ai/api/TTS/tts-websocket)

## Gemini and data key

Set `GEMINI_API_KEY` and `GEMINI_MODEL` together to enable structured Gemini extraction in real mode. Confirmations remain local. The cloud environment must permit the supported Gemini API hostname.

Generate a Fernet key privately with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Paste it into `DATA_KEY` and keep a secure copy. Use the same key for the lifetime of the database. Demo mode creates an ignored durable key file if `DATA_KEY` is empty. Use a separate `DATABASE_PATH` when testing with a different key.

## If the browser shows Listening without a transcript

Open **Voice diagnostics** in the active visit. It shows microphone capture, server receipt, Prisma frames written, peak signal level and counts for speech starts, in-progress processing, final or partial results, empty results and provider errors. These counts update on the WebSocket heartbeat. A frame written to Prisma is not a provider acknowledgement. The panel never displays audio, credentials or transcript text.

For deeper troubleshooting, start Uvicorn with the app loggers enabled at INFO. Uvicorn's access log alone does not include the app's audio diagnostics.

```bash
python -c "import logging, uvicorn; logging.basicConfig(level=logging.INFO); uvicorn.run('backend.live:create_live_app', factory=True, host='127.0.0.1', port=8765, ws_max_size=32768)"
```

Refresh the app, grant microphone access, and say a short **synthetic** visit note. Pause for Prisma's speech detector. Check these stages in order:

1. **Prisma stream ready** means the upgrade and negotiated audio configuration succeeded. A rejected upgrade logs the HTTP status. Check the Codex hostname allowlist and Gnani account access for authentication or authorization errors.
2. **Browser microphone PCM received** means the browser sent audio through ASHA's WebSocket. If this is absent, check the browser's microphone permission, selected input, and waveform. A successful capture status indicates frames are being produced, even if the input is silent.
3. **Prisma PCM frames sent** means ASHA wrote audio frames to the upstream WebSocket. Compare the windowed RMS and peak dBFS values while speaking. A window consisting of silent frames indicates digital silence. If the signal reaches Prisma but no speech event appears, tap **Finish speaking** and check for `Prisma utterance commit sent`. This asks Prisma to close an open utterance and does not save the visit.
4. **Prisma event: speech_start**, **processing**, and **transcript** identify the provider stages. An empty final gets its own log entry.

Application diagnostics record counts, event types, and HTTP status codes. Leave WebSocket wire logging disabled, since it can include credentials and payloads.

On Render, deploy the GitHub commit to the existing service, keep the existing real-mode secrets and startup command, and open the direct HTTPS app URL in current Android Chrome or iPhone Safari. Allow microphone access after tapping Start voice visit. If a service worker is serving an older copy, close existing app tabs and reopen the site after deployment.
