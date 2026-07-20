"""
Sibbu — AI Healthcare Assistant
---------------------------------
A Flask web app that provides a context-aware, health-domain-restricted
chat experience backed by Google's Gemini models via the official
`google-genai` SDK.

Sibbu only answers health and medical questions. Anything else receives a
fixed, friendly redirect rather than an LLM-generated answer, and anything
resembling a medical emergency receives an immediate safety message instead
of a normal conversational reply.

Sibbu provides general informational support only. It is not a substitute
for professional medical advice, diagnosis, or treatment. Users should
always consult a qualified healthcare provider with questions about a
medical condition.

Routes
------
  GET  /                       marketing landing page
  GET  /app                    the chat application shell
  GET  /health                 liveness/readiness probe
  GET  /api/conversations      list this browser session's conversations
  POST /api/conversations      start a new conversation, returns its id
  DELETE /api/conversations/<id>   delete one conversation
  GET  /api/conversations/<id> fetch one conversation's message history
  POST /api/chat                classic (non-streaming) reply — used by tests
                                 and as a graceful fallback if streaming fails
  POST /api/chat/stream         Server-Sent-Events streaming reply (what the
                                 UI actually uses — token-by-token like a
                                 modern chat product, not a single blocking
                                 response)
  POST /api/session/reset       wipe every conversation in this browser
                                 session (used by "Clear all chats")
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, session, stream_with_context
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from google import genai
from google.genai import errors as genai_errors

import branding
from conversation_store import ConversationStore
from domain_guard import Topic, classify_message
from security import apply_security_headers, csrf_token_is_valid, get_or_create_csrf_token

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# gemini-flash-lite-latest is the free-tier model with the most generous
# per-day quota, which matters a lot when the whole point is "$0 to run".
# Override with GEMINI_MODEL if you want a stronger (still-free) model like
# gemini-3.5-flash — see .env.example for the trade-offs.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",") if os.getenv("ALLOWED_ORIGINS") else []

MAX_MESSAGE_LENGTH = 2000
SESSION_LIFETIME_HOURS = 12

SYSTEM_INSTRUCTION = (
    f"You are {branding.BRAND_NAME}, a polite, concise healthcare information "
    "assistant. You ONLY discuss health, medical, wellness, nutrition, "
    "fitness, and mental health topics. You provide general, educational "
    "information only — you do not diagnose conditions, prescribe "
    "treatment, or recommend specific medication dosages. For anything "
    "serious, urgent, or specific to the user's individual situation, "
    "clearly recommend they consult a licensed healthcare professional. "
    "If the user asks about anything unrelated to health or medicine, "
    "politely decline and redirect them to ask a health-related question "
    "instead — do not answer the off-topic request even partially. Stay "
    "consistent in language throughout the conversation: if the user "
    "writes in Hindi, continue in Hindi; if in English, continue in "
    "English. Format answers in Markdown when it helps readability "
    "(short paragraphs, bullet lists, **bold** for key terms) but keep "
    "replies concise — this is a chat window, not an article."
)

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Create a .env file (see .env.example) "
        "with your Gemini API key before starting the app."
    )

if not SECRET_KEY:
    raise RuntimeError(
        "FLASK_SECRET_KEY is not set. Generate one with "
        "`python -c \"import secrets; print(secrets.token_hex(32))\"` "
        "and add it to your .env file."
    )

client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=SESSION_LIFETIME_HOURS)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Set SESSION_COOKIE_SECURE=true in production when served over HTTPS.
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"

if ALLOWED_ORIGINS:
    CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True)
else:
    logger.warning(
        "ALLOWED_ORIGINS not set - CORS is disabled for cross-origin requests. "
        "Same-origin requests (the bundled frontend) still work normally."
    )

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["60 per hour"],
    storage_uri="memory://",
)

store = ConversationStore(session_lifetime_hours=SESSION_LIFETIME_HOURS)


@app.after_request
def _security_headers(response):
    return apply_security_headers(response)


def _get_session_id() -> str:
    session.permanent = True
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def _require_csrf():
    """Returns an error Response if the CSRF token is missing/invalid, else None."""
    if not csrf_token_is_valid(request):
        return jsonify({"error": "Invalid or missing CSRF token. Reload the page and try again."}), 403
    return None


def _parse_chat_request():
    """Shared validation for /api/chat and /api/chat/stream.

    Returns (sid, conv, user_message, error_response). error_response is a
    Flask response tuple if validation failed, otherwise None.
    """
    if not request.is_json:
        return None, None, None, (jsonify({"error": "Request body must be JSON"}), 415)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, None, None, (jsonify({"error": "Request body must be a JSON object"}), 400)

    user_message = str(data.get("message", "")).strip()
    requested_lang = data.get("lang", "en")
    conv_id = data.get("conversation_id")

    if not user_message:
        return None, None, None, (jsonify({"error": "Message is required"}), 400)

    if len(user_message) > MAX_MESSAGE_LENGTH:
        return None, None, None, (
            jsonify({"error": f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)"}),
            400,
        )

    sid = _get_session_id()
    conv = store.get(sid, conv_id) if conv_id else None
    if conv is None:
        conv = store.new_conversation(sid)

    store.set_lang_if_unset(sid, conv.id, requested_lang)
    return sid, conv, user_message, None


def _build_prompt(history: list[dict], latest_user_message: str) -> str:
    conversation = "\n".join(f"{msg['role'].capitalize()}: {msg['content']}" for msg in history)
    return f"Conversation so far:\n{conversation}\nUser: {latest_user_message}\nAssistant:"


def _sse(event: str, data) -> str:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def landing():
    return render_template(
        "landing.html",
        brand_name=branding.BRAND_NAME,
        brand_tagline=branding.BRAND_TAGLINE,
        disclaimer=branding.DISCLAIMER,
        accent_color=branding.ACCENT_COLOR,
        suggested_prompts=branding.SUGGESTED_PROMPTS,
        repo_url=branding.REPO_URL,
    )


@app.route("/app")
def chat_app():
    sid = _get_session_id()
    csrf_token = get_or_create_csrf_token()
    conversations = store.list_conversations(sid)
    return render_template(
        "chat.html",
        brand_name=branding.BRAND_NAME,
        brand_tagline=branding.BRAND_TAGLINE,
        brand_greeting=branding.BRAND_GREETING,
        disclaimer=branding.DISCLAIMER,
        accent_color=branding.ACCENT_COLOR,
        suggested_prompts=branding.SUGGESTED_PROMPTS,
        csrf_token=csrf_token,
        conversations=conversations,
    )


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "active_sessions": store.session_size()}), 200


# ---------------------------------------------------------------------------
# Conversation management API
# ---------------------------------------------------------------------------

@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    sid = _get_session_id()
    return jsonify({"conversations": store.list_conversations(sid)})


@app.route("/api/conversations", methods=["POST"])
def create_conversation():
    csrf_error = _require_csrf()
    if csrf_error:
        return csrf_error
    sid = _get_session_id()
    conv = store.new_conversation(sid)
    return jsonify(conv.to_summary()), 201


@app.route("/api/conversations/<conv_id>", methods=["GET"])
def get_conversation(conv_id):
    sid = _get_session_id()
    history = store.get_history(sid, conv_id)
    return jsonify({"id": conv_id, "history": history})


@app.route("/api/conversations/<conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    csrf_error = _require_csrf()
    if csrf_error:
        return csrf_error
    sid = _get_session_id()
    store.delete(sid, conv_id)
    return jsonify({"message": "Conversation deleted."})


@app.route("/api/session/reset", methods=["POST"])
def reset_session():
    csrf_error = _require_csrf()
    if csrf_error:
        return csrf_error
    sid = session.get("session_id")
    if sid:
        store.clear_session(sid)
    return jsonify({"message": "All conversations cleared."})


# ---------------------------------------------------------------------------
# Chat API
# ---------------------------------------------------------------------------

@app.route("/api/chat", methods=["POST"])
@limiter.limit("15 per minute")
def chat():
    csrf_error = _require_csrf()
    if csrf_error:
        return csrf_error

    sid, conv, user_message, error = _parse_chat_request()
    if error:
        return error

    guard_result = classify_message(user_message, client=client, model=GEMINI_MODEL)
    logger.info("Message classified as %s (%s)", guard_result.topic, guard_result.reason)

    if guard_result.topic == Topic.EMERGENCY:
        reply = branding.EMERGENCY_MESSAGE
    elif guard_result.topic == Topic.GREETING:
        reply = branding.GREETING_REPLY
    elif guard_result.topic == Topic.OFF_TOPIC:
        reply = branding.OFF_TOPIC_MESSAGE
    else:
        reply = None

    if reply is not None:
        store.record_turn(sid, conv.id, "user", user_message)
        store.record_turn(sid, conv.id, "assistant", reply)
        return jsonify({"reply": reply, "topic": guard_result.topic.value, "conversation_id": conv.id})

    store.record_turn(sid, conv.id, "user", user_message)
    prompt = _build_prompt(store.get_history(sid, conv.id), user_message)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config={"system_instruction": SYSTEM_INSTRUCTION},
        )
        reply = (response.text or "").strip()
        if not reply:
            reply = "Sorry, I wasn't able to generate a response. Could you rephrase that?"
    except genai_errors.APIError:
        logger.exception("Gemini API error while handling /api/chat request")
        return jsonify({"error": "The assistant is temporarily unavailable. Please try again shortly."}), 503
    except Exception:
        logger.exception("Unexpected error while handling /api/chat request")
        return jsonify({"error": "Something went wrong. Please try again."}), 500

    store.record_turn(sid, conv.id, "assistant", reply)
    return jsonify({"reply": reply, "topic": "health", "conversation_id": conv.id})


@app.route("/api/chat/stream", methods=["POST"])
@limiter.limit("15 per minute")
def chat_stream():
    csrf_error = _require_csrf()
    if csrf_error:
        return csrf_error

    sid, conv, user_message, error = _parse_chat_request()
    if error:
        body, status = error
        return body, status

    guard_result = classify_message(user_message, client=client, model=GEMINI_MODEL)
    logger.info("Message classified as %s (%s)", guard_result.topic, guard_result.reason)

    canned_reply = {
        Topic.EMERGENCY: branding.EMERGENCY_MESSAGE,
        Topic.GREETING: branding.GREETING_REPLY,
        Topic.OFF_TOPIC: branding.OFF_TOPIC_MESSAGE,
    }.get(guard_result.topic)

    conv_id = conv.id

    if canned_reply is not None:
        store.record_turn(sid, conv_id, "user", user_message)
        store.record_turn(sid, conv_id, "assistant", canned_reply)

        def canned_gen():
            yield _sse("meta", {"conversation_id": conv_id, "topic": guard_result.topic.value})
            yield _sse("token", canned_reply)
            yield _sse("done", {"topic": guard_result.topic.value})

        return Response(stream_with_context(canned_gen()), mimetype="text/event-stream")

    store.record_turn(sid, conv_id, "user", user_message)
    prompt = _build_prompt(store.get_history(sid, conv_id), user_message)

    def generate():
        yield _sse("meta", {"conversation_id": conv_id, "topic": "health"})
        collected = []
        try:
            stream = client.models.generate_content_stream(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"system_instruction": SYSTEM_INSTRUCTION},
            )
            for chunk in stream:
                text = getattr(chunk, "text", None)
                if text:
                    collected.append(text)
                    yield _sse("token", text)
        except genai_errors.APIError:
            logger.exception("Gemini API error while streaming /api/chat/stream")
            yield _sse("error", "The assistant is temporarily unavailable. Please try again shortly.")
            return
        except Exception:
            logger.exception("Unexpected error while streaming /api/chat/stream")
            yield _sse("error", "Something went wrong. Please try again.")
            return

        reply = "".join(collected).strip()
        if not reply:
            reply = "Sorry, I wasn't able to generate a response. Could you rephrase that?"
            yield _sse("token", reply)
        store.record_turn(sid, conv_id, "assistant", reply)
        yield _sse("done", {"topic": "health"})

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(429)
def rate_limited(_error):
    return jsonify({"error": "Too many requests. Please slow down and try again shortly."}), 429


@app.errorhandler(500)
def internal_error(_error):
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=debug_mode, threaded=True)
