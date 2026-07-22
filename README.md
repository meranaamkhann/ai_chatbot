# Sibbu — AI Healthcare Assistant

A small, focused AI chat assistant that answers health, medical, wellness,
fitness, and mental-health questions — and only those. Anything else gets a
polite redirect instead of a generated answer, and anything resembling a
medical emergency gets a fixed safety message instead of a normal reply.

Built to run for **$0**: a free Gemini API key, a free PaaS tier (Render
config included), and only small, free, open-source added dependencies
(`tenacity` for retries, `cryptography` for encryption at rest, `Authlib`
for optional OAuth) — no CDN scripts, no external fonts, no paid database,
no analytics.

See **[AUDIT.md](./AUDIT.md)** for a full, itemized account of what was
wrong with the previous version of this project and exactly what changed
and why — that's the justification for every decision below, not just a
changelog.

---

## What's here

```
app.py                  Flask app: routes, request handling, SSE streaming
auth.py                 Signup/login/logout, password reset, OAuth linking, redirect safety
db.py                   SQLite connection + schema + in-place migrations
crypto.py               Fernet encryption for message content/titles at rest
conversation_store.py   Conversation data access: persistence, summarization, retention, deletion
gemini_client.py        Retry/backoff wrapper + rolling-summary generation
mailer.py                SMTP password-reset emails (free via Gmail app password)
oauth.py                 Optional Google/GitHub login via Authlib
observability.py        Structured per-request logging (latency, topic, request id)
domain_guard.py         Two-stage topic classifier (keyword pass + LLM fallback)
security.py             CSRF double-submit tokens (API + HTML forms) + security headers
branding.py             White-label config (name/tagline/colors/copy via env vars)
eval/
  eval_dataset.json      69 labeled examples incl. jailbreak/prompt-injection attempts
  run_eval.py             Confusion matrix + precision/recall report
templates/
  landing.html           Marketing page at /, with animated chat mockup
  auth.html               Login / signup / forgot-password / reset-password
  chat.html                Chat app shell at /app
  settings.html            Retention setting + account deletion
static/
  theme.css               Shared design tokens (light + dark)
  theme-toggle.js          Light/dark theme toggle, persisted
  landing.css / landing.js
  auth.css / settings.css
  chat.css / chat.js       Streaming, sidebar, composer UX
  markdown.js              Dependency-free markdown renderer for bot replies
tests/                   pytest suite (111 tests): guard logic, auth flow, password reset,
                         OAuth linking, redirect safety, retention/purge, summarization,
                         per-user data isolation, CSRF, streaming, retry logic, encryption
```

## Routes

| Method | Path | What it does |
|---|---|---|
| GET | `/` | Landing page |
| GET/POST | `/signup` | Create an account |
| GET/POST | `/login` | Log in |
| POST | `/logout` | Log out |
| GET/POST | `/forgot-password` | Request a password reset email |
| GET/POST | `/reset-password/<token>` | Set a new password from a reset link |
| GET | `/oauth/<provider>/login` | Start Google/GitHub OAuth (if configured) |
| GET | `/oauth/<provider>/callback` | Finish OAuth, log the user in |
| GET | `/app` | Chat application (login required) |
| GET/POST | `/settings` | Conversation retention setting + delete account |
| GET | `/health` | Liveness probe — actually checks database connectivity |
| GET | `/api/conversations` | List this user's conversations |
| POST | `/api/conversations` | Start a new conversation |
| GET | `/api/conversations/<id>` | Fetch one conversation's history |
| DELETE | `/api/conversations/<id>` | Delete one conversation |
| POST | `/api/session/reset` | Delete every conversation for this user |
| POST | `/api/chat` | Non-streaming reply (also the automatic fallback) |
| POST | `/api/chat/stream` | Server-Sent-Events streaming reply — what the UI uses |

All `/api/` routes require being logged in; all `POST`/`DELETE` requests
under `/api/` additionally require an `X-CSRF-Token` header matching the
token embedded in `/app` (see `security.py`).

## Running locally

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: paste a free Gemini API key from https://aistudio.google.com/app/apikey
# and generate a secret with: python -c "import secrets; print(secrets.token_hex(32))"

python app.py
# → http://127.0.0.1:5000
# a sibbu.db SQLite file is created automatically on first run
```

## Measuring the domain guard

```bash
python eval/run_eval.py
```

Reports a confusion matrix and per-class precision/recall against
`eval/eval_dataset.json`. Works without a Gemini key (keyword tier only,
ambiguous cases fail open to HEALTH) but is far more meaningful with one,
since that's what exercises the LLM fallback tier.

## Running the tests

```bash
pip install pytest
pytest -q
```

111 tests, no network calls (the Gemini client is mocked), each test run
gets its own throwaway SQLite database. Runs automatically on every push
via GitHub Actions (`.github/workflows/tests.yml`).

## Deploying for free

`render.yaml` is set up for [Render](https://render.com)'s free web service
tier:

1. Fork the repo, connect it to Render as a new Blueprint (`render.yaml` is
   picked up automatically).
2. Set the `GEMINI_API_KEY` env var in Render's dashboard (get one free at
   [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) —
   no credit card required).
3. Deploy. `FLASK_SECRET_KEY` is auto-generated by Render; everything else
   has a sane default.

The same `Procfile` (`gunicorn --worker-class gthread --workers 1 --threads
8 --timeout 120 app:app`) works unmodified on Railway, Fly.io, or any other
PaaS with a free tier — it's one process with several threads, which
matters because streaming responses need threads, and `--workers 1` keeps
every request hitting the same SQLite file on the same instance rather
than several processes contending for it. See **"Scaling beyond one
process"** in `AUDIT.md` for the upgrade path once a single free instance
isn't enough.

## Optional: password reset emails and Google/GitHub login

Both work out of the box in a reduced form — the app runs fine with
neither configured. To enable them fully:

**Password reset emails** — set `SMTP_HOST`, `SMTP_USERNAME`,
`SMTP_PASSWORD` in `.env` (a Gmail account + an "app password" works
free — Google Account → Security → 2-Step Verification → App passwords).
Without these, reset links are logged instead of emailed, which is fine
for local development.

**Google/GitHub login** — each requires creating an OAuth app in that
provider's own console; see `oauth.py`'s module docstring for the exact
steps. Set `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` and/or
`GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` in `.env`. A provider's login
button only appears on the login/signup pages once both of its variables
are set — nothing breaks or looks unfinished if you skip this.

## Rebranding (white-label)

Every piece of copy and the accent color are overridable via environment
variables — see the bottom of `.env.example`. No code changes needed to
re-skin this for a different name, clinic, or platform.

## What this is not

General health information, not medical advice. It doesn't diagnose,
prescribe, or recommend medication dosages, and it consistently says so —
that's enforced in the system prompt (`app.py`) and reinforced by the
topic guard (`domain_guard.py`), not left to the model's discretion.
