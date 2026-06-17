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
"""

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from google import genai
from google.genai import errors as genai_errors

import branding
from domain_guard import Topic, classify_message

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "").split(",") if os.getenv("ALLOWED_ORIGINS") else []

MAX_MESSAGE_LENGTH = 2000
MAX_HISTORY_MESSAGES = 20  # keep the last N messages (user + assistant combined)
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
    "English."
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

# In-memory, server-side chat store: { session_id: {"history": [...], "lang": str, "expires_at": datetime} }
# NOTE: this resets on restart and isn't shared across multiple worker processes.
# For real multi-worker / multi-instance deployment, replace this with Redis
# or another shared store (see README "Scaling Beyond a Single Process").
_chat_store: dict[str, dict] = {}


def _prune_expired_sessions() -> None:
    now = datetime.now(timezone.utc)
    expired = [sid for sid, data in _chat_store.items() if data["expires_at"] < now]
    for sid in expired:
        _chat_store.pop(sid, None)


def get_session_data() -> dict:
    """Fetch (or create) this browser session's server-side chat record."""
    session.permanent = True

    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())

    sid = session["session_id"]
    _prune_expired_sessions()

    if sid not in _chat_store:
        _chat_store[sid] = {
            "history": [],
            "lang": None,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=SESSION_LIFETIME_HOURS),
        }

    return _chat_store[sid]


def _record_turn(chat_data: dict, role: str, content: str) -> None:
    chat_data["history"].append({"role": role, "content": content})
    chat_data["history"] = chat_data["history"][-MAX_HISTORY_MESSAGES:]
    chat_data["expires_at"] = datetime.now(timezone.utc) + timedelta(hours=SESSION_LIFETIME_HOURS)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template(
        "index.html",
        brand_name=branding.BRAND_NAME,
        brand_tagline=branding.BRAND_TAGLINE,
        brand_greeting=branding.BRAND_GREETING,
        disclaimer=branding.DISCLAIMER,
        accent_color=branding.ACCENT_COLOR,
    )


@app.route("/health")
def health():
    return jsonify({"status": "healthy"}), 200


@app.route("/chat", methods=["POST"])
@limiter.limit("15 per minute")
def chat():
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON"}), 415

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    user_message = str(data.get("message", "")).strip()
    requested_lang = data.get("lang", "en")

    if not user_message:
        return jsonify({"error": "Message is required"}), 400

    if len(user_message) > MAX_MESSAGE_LENGTH:
        return jsonify({
            "error": f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)"
        }), 400

    chat_data = get_session_data()

    # Lock in the language from the first message; ignore later overrides.
    if chat_data["lang"] is None:
        chat_data["lang"] = requested_lang

    # --- Domain guard: keep Sibbu strictly on health/medical topics -----
    guard_result = classify_message(user_message, client=client, model=GEMINI_MODEL)
    logger.info("Message classified as %s (%s)", guard_result.topic, guard_result.reason)

    if guard_result.topic == Topic.EMERGENCY:
        reply = branding.EMERGENCY_MESSAGE
        _record_turn(chat_data, "user", user_message)
        _record_turn(chat_data, "assistant", reply)
        return jsonify({"reply": reply, "topic": "emergency"})

    if guard_result.topic == Topic.GREETING:
        reply = branding.GREETING_REPLY
        _record_turn(chat_data, "user", user_message)
        _record_turn(chat_data, "assistant", reply)
        return jsonify({"reply": reply, "topic": "greeting"})

    if guard_result.topic == Topic.OFF_TOPIC:
        reply = branding.OFF_TOPIC_MESSAGE
        _record_turn(chat_data, "user", user_message)
        _record_turn(chat_data, "assistant", reply)
        return jsonify({"reply": reply, "topic": "off_topic"})

    # --- In-scope health question: generate a real response -------------
    _record_turn(chat_data, "user", user_message)

    conversation = "\n".join(
        f"{msg['role'].capitalize()}: {msg['content']}" for msg in chat_data["history"]
    )
    prompt = f"Conversation so far:\n{conversation}\nAssistant:"

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
        logger.exception("Gemini API error while handling /chat request")
        return jsonify({
            "error": "The assistant is temporarily unavailable. Please try again shortly."
        }), 503
    except Exception:
        logger.exception("Unexpected error while handling /chat request")
        return jsonify({"error": "Something went wrong. Please try again."}), 500

    _record_turn(chat_data, "assistant", reply)

    return jsonify({"reply": reply, "topic": "health"})


@app.route("/clear_history", methods=["POST"])
def clear_history():
    sid = session.get("session_id")
    if sid:
        _chat_store.pop(sid, None)
    session.pop("session_id", None)
    return jsonify({"message": "Chat history cleared successfully."})


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
    app.run(host=host, port=port, debug=debug_mode)
