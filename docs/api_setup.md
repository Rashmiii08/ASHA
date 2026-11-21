# API setup

Set values in the ignored local `.env` file. Use environment settings for centrally managed values. Keep keys out of chat, code, shell history, screenshots, frontend files, and Git. Use synthetic transcripts when sending data to external providers.

## Gnani Prisma v3.0 and Timbre v3.0

The cloud proxy denied the attempted Gnani documentation pages. The checkout did not contain provider code, and no Gnani credentials were configured. The provider transport requires the official WebSocket URLs and handshake examples for the account, authentication method, Prisma audio framing and endpointing events, supported language codes, Timbre response format and end markers, and provider rate-limit guidance. Obtain these from the Gnani hackathon portal or account support.

Once verified, install a trusted Python module implementing `backend.gnani.adapter.Voice` and its `create_voice(api_key)` factory. Configure its import path in `GNANI_PROTOCOL_MODULE`, its API key in `GNANI_API_KEY`, then set `APP_MODE=real`. The plugin must translate both WebSocket protocols to the app's 16 kHz signed little-endian mono PCM16 format and manage provider stream shutdown. The app does not contain that plugin today. A key alone cannot enable live voice.

After provider configuration, verify real authentication, partial and final transcripts from the mic, provider audio playback, cancellation, and recovery on the account. Permit the exact provider hostnames in the cloud network policy if required.

## Gemini

Set `GEMINI_API_KEY` and `GEMINI_MODEL` to an enabled model that supports structured output for the supplied key. Real mode calls the asynchronous Google Gen AI client when both are set. Confirmation commands are handled locally. Live Gemini calls have yet to be verified with credentials. Cloud egress must permit the account's supported Gemini API hostname.

## Data key

Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` in a private terminal. Paste it into `DATA_KEY` and keep a secure copy. Reuse it for the lifetime of the database. Local demo mode creates an ignored durable key file beside the database if `DATA_KEY` is empty. Use a separate database path when testing a different key.
