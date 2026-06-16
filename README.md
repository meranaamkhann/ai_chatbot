# AI Healthcare Chatbot (Flask + Google Gemini)

A web-based AI healthcare information assistant built with Flask and the
Google Gemini API. It maintains conversation context and language
consistency across a session, with input validation, rate limiting, and
server-side history designed for real deployment rather than just a local
demo.

> **Disclaimer**: this assistant provides general, educational health
> information only. It is not a substitute for professional medical advice,
> diagnosis, or treatment, and it does not prescribe medication or dosages.
> Always consult a qualified healthcare provider for medical concerns, and
> contact local emergency services for urgent symptoms. This disclaimer is
> also shown directly in the app UI.

## What's in this version

This is a hardened rewrite of the original project. Notable changes:

- **Migrated to the current Gemini SDK and model.** The old `google-generativeai` package and `gemini-2.0-flash` model are both discontinued. This version uses the actively maintained `google-genai` SDK with `gemini-3.5-flash` (configurable).
- **Server-side chat history.** Conversation history is no longer stored entirely in the client-side session cookie (which has a ~4KB limit and is visible to the user). It's tracked server-side, keyed by a session ID, and trimmed to the most recent messages so token usage doesn't grow unbounded.
- **Input validation and structured errors.** Empty messages, oversized messages, non-JSON bodies, and Gemini API failures all return clear JSON errors with appropriate status codes instead of leaking raw exception text.
- **Rate limiting.** `/chat` is limited per IP to reduce abuse and control API costs.
- **No hardcoded secrets.** The Flask secret key and Gemini API key are both required environment variables; the app refuses to start without them rather than silently using an insecure default.
- **Debug mode off by default**, and the app no longer binds to `0.0.0.0` unless explicitly configured to.
- **Fixed the "Clear Chat" button**, which previously called a route that didn't exist.
- **Tests, `.env.example`, deployment config, and a synced README.**

## Project Structure

```
ai_chatbot/
├── app.py                  # Flask application
├── requirements.txt        # Pinned dependencies
├── render.yaml             # Render deployment config
├── Procfile                # Process file (gunicorn)
├── .env.example             # Template for required environment variables
├── templates/
│   └── index.html          # Frontend UI template
├── static/
│   ├── style.css           # Application styling
│   └── script.js           # Frontend interaction logic
└── tests/
    └── test_app.py         # API tests (Gemini client mocked)
```

## Technology Stack

**Backend**: Python 3, Flask, Flask-CORS, Flask-Limiter, `google-genai` (official Gemini SDK), python-dotenv, gunicorn
**Frontend**: HTML5, CSS3, vanilla JavaScript (Fetch API)
**AI Model**: Google Gemini (`gemini-3.5-flash` by default)

## Running Locally

### 1. Clone the repository

```bash
git clone https://github.com/meranaamkhann/ai_chatbot.git
cd ai_chatbot
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Then edit `.env` and fill in:
- `GEMINI_API_KEY` — get one at [Google AI Studio](https://aistudio.google.com/app/apikey)
- `FLASK_SECRET_KEY` — generate one with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

The app will refuse to start if either is missing.

### 4. Run the app

```bash
python app.py
```

The app will be available at `http://127.0.0.1:5000/`.

## API Reference

### `GET /`
Renders the chat UI.

### `GET /health`
Health check endpoint for deployment platforms. Always returns `200`.

### `POST /chat`
Sends a message and gets an AI-generated reply, with rate limiting (15 requests/minute per IP).

**Request body**

```json
{ "message": "What helps with a mild headache?" }
```

**Success response (`200`)**

```json
{ "reply": "For a mild headache, you could try resting in a quiet, dim room..." }
```

**Error responses**

| Status | Cause |
|--------|-------|
| 400    | Missing/empty message, or message exceeds 2000 characters |
| 415    | Request body is not JSON |
| 429    | Rate limit exceeded |
| 503    | Gemini API is unavailable or returned an error |
| 500    | Unexpected server error |

### `POST /clear_history`
Clears the server-side conversation history for the current session.

## Running Tests

```bash
pip install -r requirements.txt pytest
pytest
```

Tests mock the Gemini client, so they run without a real API key or network access.

## Deployment (Render)

This repo includes a `render.yaml` for deployment on [Render](https://render.com).

1. Push this repo to GitHub.
2. Go to the [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**.
3. Connect this repository — Render detects `render.yaml` automatically.
4. When prompted, paste in your `GEMINI_API_KEY` (the secret key is generated automatically).
5. Click **Apply**. Render installs dependencies and starts the app with `gunicorn app:app`.
6. Your API is live at `https://<your-service-name>.onrender.com`.

> Render's free tier spins down after inactivity and may take 30–60 seconds to wake up on the next request.

## Known Limitations / Scaling Beyond a Single Process

Chat history is currently stored in an in-memory Python dictionary inside the
Flask process. This is simple and works well for a single-instance deployment
(including Render's free tier), but it has two limits to know about:

- **History resets if the process restarts** (deploys, crashes, free-tier spin-down).
- **It isn't shared across multiple worker processes or instances.** If you scale this app horizontally (multiple gunicorn workers or multiple machines), each one would have its own separate chat store, and a user's follow-up message could land on a different worker that has no memory of their earlier messages.

For a production deployment that needs to scale beyond one process, replace
the `_chat_store` dictionary in `app.py` with a shared store such as Redis,
keyed by the same `session_id` already being generated.

## Future Improvements

- Move chat history to Redis (or a database) for multi-instance deployments
- Add streaming responses for a more responsive chat experience
- Add automated detection for messages indicating a medical emergency, with an immediate, prominent prompt to contact emergency services
- Expand language support beyond English/Hindi
