# PrettyGood AI Challenge — Twilio ↔ Gemini Live Proxy

Brief guide for running the FastAPI app that accepts Twilio Media Stream WebSocket connections, proxies audio to Google Gemini Live, and plays Gemini audio back into the call.

**Overview**
- `main.py` runs a FastAPI server with a `/media-stream` WebSocket endpoint for Twilio Media Streams and a `trigger_outbound_call()` helper to place a Twilio call.
- Incoming audio from Twilio is recorded to `call_outputs/<session>/athena_inbound.wav`.
- Gemini audio responses are recorded to `call_outputs/<session>/gemini_outbound.wav` and forwarded back to Twilio for playback.

**Requirements**
- Python 3.12 (project `.venv` recommended)
- Packages installed in the project virtualenv: `twilio`, `google-genai`, `python-dotenv`, `fastapi`, `uvicorn` (this project already uses a `.venv`)

If you need to install missing packages into the project `.venv`:

```bash
cd /workspaces/prettygoodAichallenge
source .venv/bin/activate
pip install twilio google-genai python-dotenv fastapi uvicorn
```

**Environment (.env)**
Create or edit the `.env` file in the repository root with these variables (no surrounding quotes):

```
TWILIO_ACCOUNT_SID=AC...your_sid...
TWILIO_AUTH_TOKEN=your_token
TWILIO_PHONE_NUMBER=+1234567890
TO_PHONE_NUMBER=+1987654321
GEMINI_API_KEY=AQ....
```

- Ensure phone numbers are plain E.164 strings (no quotes). The app reads these at startup with `dotenv`.

**Configuration**
- `PUBLIC_CODESPACE_URL` is defined in `main.py` and used to construct the Twilio Stream webhook. Replace it with your publicly reachable WebSocket URL (ngrok, Codespaces forwarded URL, etc.) before placing calls.

**Running the server**

```bash
cd /workspaces/prettygoodAichallenge
source .venv/bin/activate
python main.py
```

or using uvicorn directly:

```bash
./.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

The app will listen on port 8000 by default. If port 8000 is in use, stop the process or free the port:

```bash
fuser -k 8000/tcp
```

**Triggering an outbound call (test)**

```bash
cd /workspaces/prettygoodAichallenge
source .venv/bin/activate
python main.py call
```

This will use the `TO_PHONE_NUMBER` and `TWILIO_PHONE_NUMBER` from `.env`. The call is capped to ~2 minutes via `time_limit=120`.

**Call artifacts**
- Per-call folder: `call_outputs/call_YYYYMMDD_HHMMSS/`
- `athena_inbound.wav`: inbound audio from Twilio (converted to linear PCM)
- `gemini_outbound.wav`: audio produced by Gemini
- `transcript.txt`: logging and transcripts

**Multi-turn behavior**
- The code sends realtime audio to Gemini and signals end-of-turn to the Gemini Live API when Twilio’s media stream shows a gap or a `stop` event. This allows Gemini to respond multiple times in a single call.

**Common issues & troubleshooting**
- ModuleNotFoundError: make sure you run with the project `.venv` (`source .venv/bin/activate` or `./.venv/bin/python main.py`).
- `.env` values include quotes: remove surrounding quotes (e.g., `TO_PHONE_NUMBER=+1...`, not `"+1..."`).
- If Gemini does not respond after the first turn, ensure `GEMINI_API_KEY` is valid and the model supports `AUDIO` response modality.
- If you see a Gemini Live error that the modality combination is unsupported, change `responseModalities` in `main.py` to only include supported modalities (the repo uses `types.Modality.AUDIO`).

**Development notes**
- The main WebSocket handler is `handle_media_stream()` in `main.py`.
- Outbound call helper: `trigger_outbound_call()`.
- Logs and exceptions are printed to stdout; check the terminal for runtime errors.

**Next steps / Enhancements**
- Add a requirements file `requirements.txt` for reproducible installs.
- Make `PUBLIC_CODESPACE_URL` configurable via environment instead of hardcoded.
- Add automated tests and a small playback verifier to confirm Twilio hears Gemini audio.

