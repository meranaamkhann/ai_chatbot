# Audit: what was wrong, and what changed

This is a from-first-principles review of `meranaamkhann/ai_chatbot` (Sibbu)
as it stood before this pass, followed by exactly what was changed and why.
Nothing below is theoretical — every flaw was found by reading `app.py`,
`domain_guard.py`, `branding.py`, the templates, the frontend JS, and the
tests directly, and every fix was run through the test suite before being
called done.

## What the project already did right

Worth saying up front, because the fixes build on real strengths, not a
rewrite from zero:

- The two-stage domain guard (keyword pass → LLM fallback only when unsure)
  is a genuinely good pattern: it's fast and free for the common case, and
  only spends a second model call on the ambiguous minority.
- Emergency detection short-circuiting to a fixed message instead of a
  model-generated one is the correct call for a safety-critical path — no
  amount of prompt engineering is a substitute for not letting the model
  freewheel on "I can't breathe."
- The white-label `branding.py` pattern (rebrand via env vars, zero code
  changes) is a nice piece of design for a portfolio project — it signals
  "I thought about reuse," not just "I made a demo."
- Test coverage existed and was meaningful (not just smoke tests) before
  this pass, covering the guard's behavior branch by branch.

## Flaws found, and the fix for each

### 1. Single-threaded, unlocked, single-conversation chat store
**Before:** `_chat_store` was a bare module-level `dict`, mutated directly
from request handlers with no lock, and scoped to exactly one flat history
per browser session.
**Why it's a real problem:** SSE streaming (see #4) requires a threaded
worker, not gunicorn's default sync worker — the moment you add threads,
concurrent requests touching the same session become a genuine
read-modify-write race that can silently drop a message. Separately, one
history per session meant no "past conversations" sidebar was possible —
you cannot build a ChatGPT-style history UI on a data model that only
remembers one thread.
**Fix:** `conversation_store.py` — a `ConversationStore` class with a single
`threading.Lock` around every mutation, supporting many named conversations
per session, each with its own history, capped at 20 per session with
oldest-first eviction. Covered by `tests/test_conversation_store.py`,
including a concurrency regression test that hammers the same conversation
from 50 threads and asserts no turn is lost.

### 2. No CSRF defense
**Before:** the only cross-site-request protection was `SameSite=Lax` on
the session cookie. That blocks cross-site form POSTs but isn't a complete
CSRF defense for fetch-based JSON APIs on its own.
**Fix:** `security.py` implements a standard double-submit token: the
server hands the page a per-session token when `/app` renders, and every
state-changing request (`POST`/`DELETE` under `/api/`) must echo it back in
an `X-CSRF-Token` header. A cross-origin page can't read that header value
out of the page it doesn't control, so it can't forge the request. No new
dependency — the mechanism is ~15 lines.

### 3. No security headers
**Before:** no `X-Frame-Options`, `Content-Security-Policy`,
`X-Content-Type-Options`, etc. — the app was clickjackable and had no
defense-in-depth against injected scripts.
**Fix:** `apply_security_headers()` in `security.py`, applied via an
`after_request` hook. The CSP is intentionally strict (`script-src 'self'`)
because there are zero third-party scripts anywhere in the app — see #7.

### 4. No streaming — replies arrived as one blocking response
**Before:** `/chat` returned a single JSON blob after the full Gemini
response was generated, with a static "Thinking…" placeholder in the
meantime. That's a materially worse experience than ChatGPT or Claude,
where tokens appear as they're generated.
**Fix:** `POST /api/chat/stream` streams Server-Sent Events using
`client.models.generate_content_stream`. The frontend reads the response
body with `fetch()` + a `ReadableStream` reader (not `EventSource`, which
can't send a POST body) and renders tokens as they arrive. The classic
`POST /api/chat` endpoint is kept as an automatic one-shot fallback if
streaming fails outright (e.g. a proxy that buffers the whole response),
so the chat never just dies.

### 5. Gunicorn config couldn't actually serve streaming responses well
**Before:** `Procfile` / `render.yaml` ran plain `gunicorn app:app` — a
single sync worker processes one request at a time, so a long-lived SSE
connection would block every other request on that worker for its entire
duration.
**Fix:** `gunicorn --worker-class gthread --workers 1 --threads 8 --timeout
120`. One worker process on purpose — the store is in-process memory (see
#1's design note), so multiple *processes* would each have their own
inconsistent copy; threads within that one process share the same store
safely because of the lock. This is still a $0 change: gthread ships with
gunicorn, no new dependency.

### 6. No markdown rendering — replies were `textContent`, always
**Before:** bot replies were inserted with `element.textContent = text`,
so any structure the model produced (lists, bold, code) rendered as raw
asterisks and backticks.
**Fix:** `static/markdown.js`, a small dependency-free markdown-to-HTML
renderer (bold, italic, inline code, fenced code blocks with a copy
button, lists, headings, links restricted to `http(s)://`). It HTML-escapes
the raw text *before* interpreting any markdown syntax, so model output —
which is effectively untrusted input from the app's point of view — can't
inject markup through the very escaping step meant to render it safely.
No CDN dependency (see #7).

### 7. Everything now runs with zero third-party runtime dependencies
This wasn't a flaw in the original per se, but it's a deliberate constraint
kept throughout every fix above: no marked.js/showdown from a CDN for
markdown, no Google Fonts, no analytics, nothing loaded from outside
`'self'`. That's what makes the strict CSP in #3 possible, and it's in
service of the actual ask — an app that costs nothing and depends on
nothing else being up.

### 8. One conversation thread, no history sidebar
**Before:** the sidebar's "Recent questions" panel just listed strings
from the *current* session's messages — closing the tab or starting a new
chat lost them. There was no way to have two separate conversations.
**Fix:** built on the new `ConversationStore` (#1): `GET/POST
/api/conversations`, `GET/DELETE /api/conversations/<id>`, and `POST
/api/session/reset`. The sidebar in `chat.html`/`chat.js` lists real,
switchable, independently-titled conversations (title auto-derived from
the first message, like ChatGPT's), not a scrollback of raw strings.

### 9. No landing/marketing page — the chat itself was the only page at `/`
**Before:** a first-time visitor landed directly in an empty chat window
with no explanation of what the product does, why it's safe, or what it
can't do.
**Fix:** `templates/landing.html` at `/`, chat app moved to `/app`. The
landing page explains the three-stage safety model (topic guard, emergency
detection, session-only memory) in plain terms, gives tappable example
prompts that deep-link into `/app?prompt=...`, and is explicit that the
whole thing is free to run and open-source.

### 10. Model default and free-tier fit
The original `GEMINI_MODEL` default was `gemini-3.5-flash`, which is a real
current model but not the free tier's most generous option. The default is
now `gemini-flash-lite-latest` — an auto-updating alias that currently
resolves to the free tier's highest-RPM/RPD model, which matters directly
for "must stay free to run." `.env.example` documents the trade-off if you
want to switch to a stronger (still-free) model.

## What was deliberately *not* changed

- **The domain guard's classification logic** (`domain_guard.py`) — it was
  already correct and well-tested; the only thing wired differently is
  that both `/api/chat` and `/api/chat/stream` call the same
  `classify_message()` function, so the safety behavior is identical on
  both paths.
- **No database.** Conversations are still in-memory. That's a scaling
  limit, documented in `conversation_store.py`'s docstring, not an
  oversight — a managed Postgres/Redis instance is the natural next step
  the moment this needs to survive a restart or run on more than one
  instance, but it also stops being free, which was an explicit constraint
  here.
- **No user accounts.** Anonymous, session-scoped conversations keep the
  whole app at zero infrastructure cost and zero PII to protect. Worth
  revisiting only if a real deployment needs cross-device history.

## Scaling beyond one process (when you outgrow the free tier)

When a single Render/Railway free instance stops being enough:
1. Swap `ConversationStore`'s internal `dict` for a Redis-backed
   implementation behind the same public methods (`new_conversation`,
   `record_turn`, `get_history`, …) — the rest of the app doesn't need to
   change, because it already only talks to the store through that
   interface.
2. Move `Limiter`'s `storage_uri` from `memory://` to the same Redis
   instance, so rate limits are consistent across processes/instances.
3. Only then raise `--workers` above 1.

---

## Round 2: accounts, persistence, retries, observability, and a measured guard

The first pass fixed the app's plumbing (concurrency, CSRF, streaming, no
markdown). This pass targets what was still missing for "backend AI/ML
engineer" work specifically: does it survive a restart, does it recover
from a flaky upstream call, can you actually measure whether the safety
classifier works, and can more than one person use it.

### 11. No user accounts — conversations were tied to an anonymous browser session
**Before:** anyone opening the app got an anonymous session; there was no
way to log back in from a different device, and clearing cookies meant
losing every conversation.
**Fix:** `auth.py` — email/password accounts, hashed with
`werkzeug.security.generate_password_hash` (PBKDF2-SHA256, already a Flask
dependency — no new package for hashing). `/signup`, `/login`, `/logout`,
and `/app` is now behind `@login_required`. Deliberately minimal: no OAuth,
no email verification, no password reset. Those are real gaps for a
production auth system — named here rather than silently omitted — and
the natural next additions once this needs to be more than a portfolio
deployment.

### 12. In-memory conversation store — data died on every restart
**Before:** even after Round 1's thread-safety fix, `ConversationStore`
was still a Python dict in process memory. Any restart (a deploy, a crash,
a free-tier idle spin-down) silently wiped every conversation for every
user. That's a real data-loss bug, not just a scaling limitation.
**Fix:** `db.py` + rewritten `conversation_store.py` — SQLite, a single
file on disk, in the Python standard library (`sqlite3`), so this is a
zero-cost change. Conversations and messages are now relational tables
keyed by `user_id`, with a foreign key and `ON DELETE CASCADE` so deleting
a user's account (were that feature added) can't orphan rows. Verified
with a regression test (`test_persists_across_reconnect`) that tears down
and reopens the DB connection mid-test and asserts the data is still
there — the literal scenario that used to lose data.
**Honest caveat, not glossed over:** Render's free web-service tier has an
*ephemeral* filesystem — it persists while the instance stays warm, but a
redeploy or a free-tier idle spin-down still wipes the SQLite file, same
as before. This is a real limit of "$0 hosting," not something this fix
silently claims to solve. `.env.example` documents the workaround (a small
free persistent volume on Railway/Fly, or a free hosted Postgres like Neon)
for anyone who needs durability past a single Render instance's lifetime.

### 13. No retry on transient Gemini failures
**Before:** a single 503 or dropped connection from Gemini was a failed
reply shown to the user, full stop — no distinction between "the API key
is wrong" (not worth retrying) and "the upstream hiccuped" (worth retrying).
**Fix:** `gemini_client.py`, using `tenacity` (added as an explicit,
pinned, free dependency — it was previously only an undeclared transitive
dependency of `google-genai`, which is fragile to rely on). Retries on
429/500/502/503/504 with exponential backoff, capped at 3 attempts;
4xx auth/validation errors are not retried, since retrying a bad API key
just burns quota on a guaranteed failure. For streaming specifically, only
the attempt to fetch the *first* chunk is retried — once tokens have
started reaching the client, restarting generation from scratch would mean
silently replaying text already shown, which is worse than surfacing the
error. Full resumable mid-stream retry is a real, named gap, not solved
here.

### 14. No observability — a slow or failing request was invisible
**Before:** no per-request timing, no way to tell whether a slow chat
reply was Gemini being slow or the app's own code, no request id to
reference when debugging a specific failure.
**Fix:** `observability.py` — structured log line per request (method,
path, status, total latency, and for chat routes, which topic the guard
assigned and how long the Gemini call itself took, separate from total
request time). Every response also carries an `X-Request-Id` header. No
APM/Prometheus/paid service added — that's a deliberate scope cut for a
free-tier project, named as the natural next step once grepping logs
stops being enough.

### 15. The domain guard had never been measured, only unit-tested
**Before:** `domain_guard.py` had solid unit tests for individual branches
of its logic, but no measurement of end-to-end classification accuracy —
"the code does what the code says" is not the same claim as "the
classifier is right most of the time."
**Fix:** `eval/eval_dataset.json` (59 hand-labeled examples across
health/off-topic/emergency/greeting, including adversarial phrasing meant
to miss the keyword tier and force the LLM fallback) plus
`eval/run_eval.py`, which reports a confusion matrix, per-class
precision/recall, and every misclassified example. Explicitly documented
as a small, honest measurement — enough to catch regressions and give a
real number to cite, not a claim of clinical-grade validation. Run without
a Gemini key, it also usefully demonstrates the guard's fail-open behavior
on ambiguous input (accuracy drops to ~78% because every "unsure" case
defaults to HEALTH rather than being resolved by the classifier) — with a
real API key, the LLM fallback tier resolves most of those correctly.

### What's still explicitly not done
- No OAuth/SSO, no password reset, no email verification.
- No true resumable mid-stream retry (see #13).
- No managed metrics/alerting — logs only (see #14).
- SQLite, not Postgres — the right call for $0 hosting, wrong call the
  moment this needs multi-instance writes or serious concurrent load. The
  swap path is documented in `conversation_store.py`'s docstring and only
  touches that one file's internals, not its public function signatures.
