# Sibbu — AI Healthcare Assistant

Sibbu is a domain-restricted AI healthcare assistant built with Flask and
Google's Gemini API. It only answers health and medical questions — anything
else gets a polite, fixed redirect instead of an AI-generated answer, and
anything resembling a medical emergency gets an immediate safety message
instead of a normal reply.

It's built as a **white-label-ready product**: branding (name, tagline,
colors, disclaimer text) lives in one config file (`branding.py`) and can be
overridden entirely via environment variables, so the same codebase can be
rebranded for a specific hospital, clinic, or health platform without
touching application logic.

> **Disclaimer**: Sibbu provides general, educational health information
> only. It is not a substitute for professional medical advice, diagnosis,
> or treatment, and it does not prescribe medication or dosages. Always
> consult a qualified healthcare provider for medical concerns, and contact
> local emergency services for urgent symptoms. This disclaimer is also
> shown directly in the app UI.

<!--
  Add a real screenshot or short screen recording here once deployed, e.g.:
  ![Sibbu chat UI](docs/screenshot.png)
-->

## What Makes This "Industry Level"

- **Strict domain restriction.** A two-stage guard (fast keyword pre-filter,
  then an LLM fallback classifier for ambiguous cases) keeps every reply
  on-topic — health questions get a real AI response; everything else gets
  a fixed redirect message; emergencies get an immediate safety message.
  See [`domain_guard.py`](domain_guard.py).
- **White-label branding.** All user-facing text and the accent color are
  centralized in [`branding.py`](branding.py) and overridable via env vars —
  rebranding for a client is a config change, not a code change.
- **Current, supported SDK and model.** Uses the actively maintained
  `google-genai` SDK (the older `google-generativeai` package and the
  `gemini-2.0-flash` model are both discontinued).
- **No secrets in source.** `GEMINI_API_KEY` and `FLASK_SECRET_KEY` are
  required environment variables; the app refuses to start with a clear
  error if either is missing.
- **Server-side chat history**, trimmed to recent turns, so a long
  conversation doesn't blow past the cookie size limit or grow API costs
  unbounded.
- **Input validation, rate limiting, and structured errors** instead of
  raw stack traces reaching the client.
- **Automated tests** (30 cases) covering the domain guard and the full API,
  with the Gemini client mocked so the suite runs without a real API key.
- **One-click deployment config** for Render.

## Project Structure

```
ai_chatbot/
├── app.py                  # Flask application and routes
├── domain_guard.py         # Health/off-topic/emergency message classifier
├── branding.py             # White-label branding configuration
├── requirements.txt        # Pinned dependencies
├── render.yaml             # Render deployment config
├── Procfile                # Process file (gunicorn)
├── LICENSE                 # MIT license
├── .env.example             # Template for environment variables
├── templates/
│   └── index.html          # Chat UI template
├── static/
│   ├── style.css           # Application styling
│   └── script.js           # Frontend interaction logic
└── tests/
    ├── test_app.py          # API tests (Gemini client mocked)
    └── test_domain_guard.py # Domain guard unit tests
```

## How the Domain Guard Works

Every incoming message is classified before Sibbu decides how to respond:

1. **Keyword pass** (instant, free): checks the message against curated
   health and off-topic keyword/phrase lists, and against emergency
   patterns (e.g. chest pain + breathing difficulty, suicidal ideation,
   overdose). Most messages — clearly health-related, or clearly not — are
   resolved here with zero extra API calls.
2. **LLM fallback classifier** (only when the keyword pass is ambiguous):
   a small, cheap Gemini call with `temperature=0` classifies the message
   as `HEALTH`, `OFF_TOPIC`, or `EMERGENCY`.
3. **Fail-open design**: if the fallback classifier itself errors (e.g. the
   Gemini API is briefly down), Sibbu defaults to treating the message as a
   health question rather than blocking it outright — the system
   instruction given to the main model is a second layer that still
   constrains it to health topics, so this fail-open behavior doesn't
   bypass the restriction, it just avoids a false block during a transient
   classifier failure.

Based on the result:

| Classification | Behavior |
|-----------------|----------|
| `HEALTH`        | Forwarded to Gemini with the full conversation context and a system instruction restricting it to general health information |
| `OFF_TOPIC`      | Returns a fixed redirect message (`branding.OFF_TOPIC_MESSAGE`) — no Gemini call for the reply itself |
| `EMERGENCY`      | Returns a fixed safety message (`branding.EMERGENCY_MESSAGE`) directing the user to emergency services — no Gemini call |

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

Edit `.env` and fill in:
- `GEMINI_API_KEY` — get one at [Google AI Studio](https://aistudio.google.com/app/apikey)
- `FLASK_SECRET_KEY` — generate one with:
  ```bash
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

The app refuses to start if either is missing.

### 4. Run the app

```bash
python app.py
```

The app will be available at `http://127.0.0.1:5000/`.

## Rebranding Sibbu for a Specific Client

Sibbu is designed to be relicensed under a different name without code
changes. Set any of the following environment variables (all optional —
unset ones fall back to Sibbu's defaults):

```bash
BRAND_NAME="Meridian Health"
BRAND_TAGLINE="Your hospital's AI assistant"
BRAND_ACCENT_COLOR="#1d4ed8"
BRAND_DISCLAIMER="Meridian Health provides general health information only..."
BRAND_OFF_TOPIC_MESSAGE="Sorry, I can only help with health questions for Meridian Health patients."
BRAND_EMERGENCY_MESSAGE="This may be a medical emergency. Please call 911 or visit our ER immediately."
```

See [`branding.py`](branding.py) for the full list and defaults.

## API Reference

### `GET /`
Renders the chat UI.

### `GET /health`
Health check endpoint for deployment platforms. Always returns `200`.

### `POST /chat`
Sends a message and gets a response, with rate limiting (15 requests/minute per IP).

**Request body**

```json
{ "message": "What helps with a mild headache?" }
```

**Success response (`200`)**

```json
{ "reply": "For a mild headache, you could try resting in a quiet, dim room...", "topic": "health" }
```

The `topic` field is one of `health`, `off_topic`, or `emergency`, so a
frontend can style or log responses differently (Sibbu's own UI uses it to
visually flag emergency messages).

**Off-topic example**

```json
{ "reply": "Sorry, I can only answer health and medical related questions...", "topic": "off_topic" }
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

30 tests cover the domain guard (keyword classification, LLM fallback,
emergency detection, fail-open behavior) and the full API (health questions,
off-topic redirects, emergency responses, validation, rate limiting paths,
and error handling). The Gemini client is mocked throughout, so the suite
runs without a real API key or network access.

## Deployment (Render)

This repo includes a `render.yaml` for deployment on [Render](https://render.com).

1. Push this repo to GitHub.
2. Go to the [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**.
3. Connect this repository — Render detects `render.yaml` automatically.
4. When prompted, paste in your `GEMINI_API_KEY` (the secret key is generated automatically).
5. Click **Apply**. Render installs dependencies and starts the app with `gunicorn app:app`.
6. Your API is live at `https://<your-service-name>.onrender.com`.

> Render's free tier spins down after inactivity and may take 30–60 seconds
> to wake up on the next request — worth mentioning if you link this from a
> portfolio, so the first click isn't mistaken for a broken app.

## Known Limitations / Scaling Beyond a Single Process

Chat history is stored in an in-memory Python dictionary inside the Flask
process. This is simple and works well for a single-instance deployment
(including Render's free tier), but:

- **History resets if the process restarts** (deploys, crashes, free-tier spin-down).
- **It isn't shared across multiple worker processes or instances.** Scaling
  horizontally (multiple gunicorn workers or multiple machines) would need a
  shared store — replace `_chat_store` in `app.py` with Redis or a database,
  keyed by the same `session_id` already being generated.

The keyword lists in `domain_guard.py` are a starting point, not exhaustive —
real-world traffic will surface phrasings worth adding over time. The LLM
fallback classifier exists specifically to catch what the keyword lists miss.

## Future Improvements

- Move chat history to Redis (or a database) for multi-instance deployments
- Add streaming responses for a more responsive chat experience
- Expand emergency detection patterns based on real usage
- Add a lightweight admin view of classifier decisions for tuning the keyword lists
- Expand language support beyond English/Hindi

## License

MIT — see [LICENSE](LICENSE).
