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

Open **Voice diagnostics** in the active visit. It shows microphone capture, server receipt, Prisma frames written, peak signal level and counts for speech starts, in-progress processing, final or partial results, empty results and provider errors. These counts update on the WebSocket heartbeat. A frame written to Prisma is not a provider acknowledgement. Recoverable errors, malformed results and empty final results show an immediate explanation in the visit and in Voice diagnostics. A successful final clears it. Browser transcript status distinguishes a final received by the server from nonempty text displayed in this browser. The last displayed final stays visible after stopping. A resumed WebSocket can replay the latest final from server session memory without running the visit workflow again. The panel never displays audio, credentials, transcript text or provider error details.

For deeper troubleshooting, start Uvicorn with the app loggers enabled at INFO. Uvicorn's access log alone does not include the app's audio diagnostics.

```bash
python -c "import logging, uvicorn; logging.basicConfig(level=logging.INFO); uvicorn.run('backend.live:create_live_app', factory=True, host='127.0.0.1', port=8765, ws_max_size=32768)"
```

Refresh the app, grant microphone access, and say a short **synthetic** visit note. Pause for Prisma's speech detector. Check these stages in order:

1. **Prisma stream ready** means the upgrade and negotiated audio configuration succeeded. A rejected upgrade logs the HTTP status. Check the Codex hostname allowlist and Gnani account access for authentication or authorization errors.
2. **Browser microphone PCM received** means the browser sent audio through ASHA's WebSocket. If this is absent, check the browser's microphone permission, selected input, and waveform. A successful capture status indicates frames are being produced, even if the input is silent.
3. **Prisma PCM frames sent** means ASHA wrote audio frames to the upstream WebSocket. Compare the windowed RMS and peak dBFS values while speaking. A window consisting of silent frames indicates digital silence. If the signal reaches Prisma but no speech event appears, tap **Finish speaking** and check for `Prisma utterance ended with paced silence frames`. This asks Prisma to close an open utterance and does not save the visit.
4. **Prisma event: speech_start**, **processing**, and **transcript** identify the provider stages. An empty final gets its own log entry.

Application diagnostics record counts, event types, and HTTP status codes. Leave WebSocket wire logging disabled, since it can include credentials and payloads.

On Render, deploy the GitHub commit to the existing service, keep the existing real-mode secrets and startup command, and open the direct HTTPS app URL in current Android Chrome or iPhone Safari. Allow microphone access after tapping Start voice visit. If a service worker is serving an older copy, close existing app tabs and reopen the site after deployment.

## Persistent visit storage on Render

The repository defaults to `DATABASE_PATH=data/visits.sqlite3` under the application checkout and does not declare a Render disk. Render service files outside a persistent disk mount are ephemeral. Verify the service's **Disks** tab and the effective `DATABASE_PATH` before relying on retained visits.

For a single-instance Render service with a persistent disk, mount a disk at a path such as `/var/data`, set `DATABASE_PATH=/var/data/visits.sqlite3`, and keep the same `DATA_KEY` in Render secrets. If the old database holds visits, make a consistent SQLite backup before deploying or changing paths and restore that backup to the new disk. Verify the restored register with the existing key before accepting new visits. Keep encrypted backups separately. No database or Render settings are migrated by this application change.

If the service plan cannot attach a disk or the app needs multiple instances, use managed PostgreSQL with an explicit migration that retains Fernet-encrypted visit payloads. The current code uses SQLite and does not connect to PostgreSQL.

[Render persistent disks](https://render.com/docs/disks)

## Deploy the visit validation and scheduling update

Back up the existing encrypted SQLite database before deploying this release. Deploy `main` to the existing Render service with its current `DATA_KEY`, Gnani credentials, startup command, and persistent `DATABASE_PATH`. Do not set a new encryption key for an existing register. The update applies the four-digit ID and newborn weight rules to new writes. Existing records can still be listed and their follow-ups completed. The release does not rewrite or migrate the database on startup.

Spoken relative dates are resolved using India Standard Time. Calendar-month offsets clamp to the last day of a shorter month. **Next week** means seven days later. A month-and-day date without a year uses the current year and needs clarification if it has already passed. Vaccination dates are based only on the worker’s input. An unnamed vaccination date appears as a vaccination follow-up.

After deployment, use a synthetic four-digit beneficiary ID to verify voice capture, a relative next-visit date, a vaccination reminder, explicit **Save Visit**, and completion from **Follow-ups**. Check Visit History after restarting the Render service to verify that its configured storage actually persists.

## Deploy visit location on Render

Deploy `main` on the existing Render service. Keep the same `DATABASE_PATH`, `DATA_KEY`, and Gnani secrets. The optional GPS object lives inside the existing Fernet-encrypted payload. This update adds no SQLite columns and does not rewrite earlier visits. Keep the same persistent disk and usual database backup procedure.

Open the public HTTPS URL and verify one synthetic visit. The app requests one browser geolocation reading when the worker starts a new visit or taps **Refresh Location**. An unavailable, denied, timed-out, or skipped reading does not prevent a save. Draft coordinates stay in server session memory and are recovered with the existing draft session. They are neither persisted in browser storage nor included in speech extraction or diagnostics.

Location input and retrieval require the app to see HTTPS/WSS. If Render terminates TLS before Uvicorn and location is rejected or the saved map link is missing, check that Uvicorn receives and trusts the proxy's forwarded protocol headers. Use `--proxy-headers` with `--forwarded-allow-ips` restricted to your trusted ingress IPs or CIDRs. Keep the public endpoint on HTTPS. Serve this prototype within its existing trusted deployment and use synthetic data for testing.

The preview uses locally bundled Leaflet 1.9.4 and OpenStreetMap tiles. Tiles are requested only after the worker chooses **Show map**, which reveals the surrounding map area to OpenStreetMap. Opening a saved map link shares the pinned coordinates. The app does not reverse-geocode coordinates or substitute an IP-based location. Follow the tile usage policy for the deployed traffic volume. [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/)

## Deploy worker authentication on Render

1. Verify that the SQLite file is on the existing Render persistent disk. Use an absolute `DATABASE_PATH` inside the mount. Back up the existing encrypted database and keep the current `DATA_KEY` and Gnani variables. Accounts, sessions, reset codes, and visit ownership live in that same database. Ephemeral storage loses them on redeploy.
2. Deploy `main` on a single instance and serve the public app over HTTPS. Configure Uvicorn to trust forwarded protocol and client-IP headers only from the actual ingress proxy. Authenticated API requests require HTTPS and voice requires WSS. Use the existing credentials and secrets. No additional production secret is required.
3. In the trusted interactive Render shell connected to the same disk and environment, run `python -m scripts.bootstrap_admin`. Enter the administrator details and a unique password of at least 12 characters when prompted. The password is entered without echo. This command creates the first administrator once. Public registration cannot create an administrator. Sign in as administrator with the registered mobile number and retain the generated internal recovery ID privately.
4. Workers use **Register as a worker** and can sign in immediately with the registered mobile number and password. The number is a sign-in identifier. Registration does not verify ownership by SMS. Administrators can update assigned areas or deactivate accounts. Deactivation revokes sessions and pending reset codes.
5. Existing visits remain unassigned and invisible to workers. In **Unassigned legacy visits**, compare each record ID against an authorized migration roster and assign it explicitly. Ownership assignment leaves the encrypted payload intact. Area changes alone do not transfer visit ownership.
6. **Forgot password** requests assistance. After independently verifying the worker's identity through an approved channel, an administrator can issue a 15-minute, single-use code and share it privately with that worker. Enter it in **Enter a reset code**. No SMS is sent by this app. A password reset revokes previous sessions. Keep codes out of URLs, screenshots, tickets, and logs. For a verified sole administrator, a trusted operator can run `python -m scripts.bootstrap_admin --recover` in the same interactive service shell. Recovery revokes that administrator’s old sessions.
7. Verify registration, sign-in, GPS capture, Save Visit, and sign-out with synthetic data. Confirm that a second worker cannot see or modify the first worker's records. Restart the Render service and verify persistence before relying on the deployment.

Draft content remains in the existing in-memory server session. The browser stores only an account-scoped draft ID in session storage, which cannot authenticate a user. Re-authenticate as the same worker to resume while the server draft still exists. A server restart or draft expiry removes that recovery path.

## Configure optional approximate address lookup

Set `ASHA_REVERSE_GEOCODER_URL` to the HTTPS reverse endpoint of a private or contracted Nominatim-compatible service authorized to handle approximate visit locations. Keep the existing Gnani credentials and `DATA_KEY`. The public OpenStreetMap Nominatim endpoint is intentionally rejected because visit locations are sensitive. The public service prohibits submitting personal or confidential data. [Public Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) Leave the setting empty for GPS coordinates and maps without address lookup.

The worker opts into address lookup before capture or refresh. After GPS permission succeeds, ASHA sends coordinates rounded to two decimal places and requests a locality-level result. The backend limits lookup to one request per second per app process and caches successful results in memory for 24 hours, up to 512 entries. Confirm that the configured service's contract permits this retention and traffic. Failed or busy lookups leave coordinates available for saving. Map tiles remain separately opt-in. No lookup credentials are sent to the browser.

Addresses are stored in the same encrypted visit payload. Existing payloads need no migration. On Render, retain the persistent `DATABASE_PATH`, then verify a synthetic visit with and without consent and restart the service to check persistence.

For spoken dates, **next month** means one calendar month from the current Asia/Kolkata date, clamped to the last day of the destination month. During review, enter a corrected next-visit date or select **No follow-up required** to clear an invalid spoken date.


### Mobile sign-in migration

Deploy on the existing database with the same `DATA_KEY`. On startup, the app adds a keyed mobile lookup index derived from existing encrypted profiles. The migration keeps each internal worker ID, existing account status, password hash, and visit owner unchanged. Existing active users sign in with the mobile number they registered, with or without `+91`. Existing pending or inactive accounts retain their status.

If a number was shared by multiple legacy accounts, sign-in and password reset through that number require administrator resolution. Independently verify each account and update its mobile number in Worker administration. An administrator who shares a number can use the trusted terminal recovery command to set a verified replacement mobile. Changing a mobile number revokes the affected account’s sessions and reset codes. New registrations cannot claim a number already in the register.
