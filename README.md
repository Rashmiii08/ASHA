# Voice Field Register

Voice Field Register helps ASHA workers turn home-visit notes into a structured register, check predefined referral rules, review corrections and manage follow-ups. Use synthetic records exclusively. The checkout implements a working synthetic transcript demo. Live Gnani voice requires the official Gnani WebSocket protocol and a provider implementation.

The app uses Python 3.12, FastAPI, WebSockets, Pydantic, Google Gen AI, SQLite, Fernet and a vanilla JavaScript PWA with Web Audio. See the [architecture and WebSocket contract](docs/architecture.md).

## Run on Windows PowerShell

Open a terminal inside the ASHA project directory. Install Python 3.12 first if needed.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn backend.live:create_live_app --factory --host 127.0.0.1 --port 8765 --ws-max-size 32768
```

Copy the example only on first setup. Open `http://127.0.0.1:8765` in Chrome or Edge. Select a language and use **Start voice visit**, then **Use sample** and **Send transcript**. The demo displays the assistant's reply as text. Use `Ctrl+C` in the terminal to stop the server.

For macOS or Linux, create the environment with `python3.12 -m venv .venv` and use `.venv/bin/python` in place of the Windows Python path. On port conflicts, use `Get-NetTCPConnection -LocalPort 8765 -State Listen` and inspect `OwningProcess` with `Get-Process -Id <number>`, or choose another port and update the browser address. Microphone capture and PWA installation require localhost or a secure HTTPS origin. The app is intended for a trusted, single-user local environment with synthetic data. A shared deployment requires authentication, access controls and a security review.

## API keys and modes

See [API setup](docs/api_setup.md) for Gnani Prisma v3.0, Gnani Timbre v3.0, Gemini and the persistent data key. Edit `.env` locally, or configure secrets in the hosting environment. Real mode requires `DATA_KEY` and reports missing Gnani configuration. It never routes speech through a browser or alternate provider.

## Testing and evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.score_clips
```

The evaluation script uses four synthetic text fixtures for field extraction, confirmed saves, corrections and referral recall. It reports no first-audio measurement because no audio provider is involved. Use the [demo guide](docs/demo_guide.md) for the walkthrough, live measurements and pitch instructions.

The optional `node tests/audio_worklet.cjs` check verifies PCM chunking and resampling at 44.1 and 48 kHz. The Playwright check in `tests/browser_smoke.cjs` requires Node, Playwright, Chromium, and a running demo server with an empty temporary database. Set `FIELD_REGISTER_TEST_URL` if the server uses a port other than 8765.

## Responsible AI and offline behavior

The app supports documentation and fixed referral wording. Current referral logic covers a narrow newborn subset: inability to feed, convulsions, a temperature at or above 37.5 °C, and a temperature below 35.5 °C. Scope and thresholds are based on the WHO *Integrated Management of Childhood Illness Chart Booklet* (2014), **Assess, classify and treat the sick young infant**, pages 2–3. The source could not be fetched from the cloud session and the English/Hindi phrases need clinical and language review before field use. Other visit categories have no implemented referral checklist. Treat the demo as synthetic documentation software only. https://iris.who.int/handle/10665/104772

The app shell remains available after a successful online load. Live AI and history refresh need the network. A short-lived browser session draft contains structured synthetic values and the reconnect token in plaintext session storage. The server keeps in-progress conversations in memory, so restarting the backend expires its resumable drafts. Stored visits and transcripts are encrypted in SQLite. Record IDs and creation timestamps are plaintext metadata.

**Delete all demo visits** clears confirmed records and discards the current browser draft. For local cleanup, stop the server and run `python -m scripts.delete_all_data --yes` from the repository using the installed virtual environment. This clears visit rows in the configured database and compacts it. The key, database schema, other browser sessions and backups are outside that command's scope. Closing the tab clears its session storage.
