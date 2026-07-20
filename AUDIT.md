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
