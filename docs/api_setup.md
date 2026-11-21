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

Prisma is configured through upgrade headers and acknowledges with `connected`. The adapter batches microphone data into 1,024-byte raw PCM frames and submits completed `transcript` segments to the ASHA dialogue. The supplied Prisma PDF was truncated after page 19 of 34. The recovered pages and published SDK document completed segments. Support for a distinct partial-transcript event is pending a complete reference.

Timbre uses the documented v3.0 voice Shivani and requests raw 16 kHz mono PCM16. The adapter streams base64-decoded `audio` messages and stops on `complete`, cancellation or error. The existing ASHA language picker offers English and Hindi. Provider request construction for Kannada is covered by a mock test.

[Prisma WebSocket reference](https://docs.gnani.ai/api/STT/stt-websocket) · [Timbre WebSocket reference](https://docs.gnani.ai/api/TTS/tts-websocket)

## Gemini and data key

Set `GEMINI_API_KEY` and `GEMINI_MODEL` together to enable structured Gemini extraction in real mode. Confirmations remain local. The cloud environment must permit the supported Gemini API hostname.

Generate a Fernet key privately with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Paste it into `DATA_KEY` and keep a secure copy. Use the same key for the lifetime of the database. Demo mode creates an ignored durable key file if `DATA_KEY` is empty. Use a separate `DATABASE_PATH` when testing with a different key.
