import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import branding


@pytest.fixture
def app_module(monkeypatch):
    """Import app.py fresh with required env vars set, a throwaway SQLite DB,
    and the Gemini client mocked out."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("ENCRYPTION_KEY", "tkGtljKdQPNaeYmt3KQr--k-_13LdA-qc0tUjoeDpLY=")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-flash-lite-latest")

    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    for mod in ("app", "db", "conversation_store", "auth", "crypto"):
        sys.modules.pop(mod, None)

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This is a test reply about your health question."
        mock_client.models.generate_content.return_value = mock_response

        def fake_stream(**kwargs):
            chunk = MagicMock()
            chunk.text = "This is a test reply about your health question."
            return iter([chunk])

        mock_client.models.generate_content_stream.side_effect = fake_stream
        mock_client_cls.return_value = mock_client

        import app as app_module  # noqa: PLC0415

        app_module.client = mock_client
        yield app_module

    for mod in ("app", "db", "conversation_store", "auth", "crypto"):
        sys.modules.pop(mod, None)


@pytest.fixture
def client(app_module):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def signup(client, email="test@example.com", password="password123"):
    return client.post("/signup", data={"email": email, "password": password}, follow_redirects=False)


def login(client, email="test@example.com", password="password123"):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def get_csrf_token(client) -> str:
    response = client.get("/app")
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    assert match, "csrf token meta tag not found on /app"
    return match.group(1)


def signup_and_get_csrf(client, email="test@example.com", password="password123") -> str:
    signup(client, email, password)
    return get_csrf_token(client)


def post_json(client, path, payload, csrf_token=None):
    headers = {"X-CSRF-Token": csrf_token} if csrf_token else {}
    return client.post(path, json=payload, headers=headers)


# ---------------------------------------------------------------------------
# Pages / auth
# ---------------------------------------------------------------------------

def test_landing_page_loads_logged_out(client, app_module):
    response = client.get("/")
    assert response.status_code == 200
    assert app_module.branding.BRAND_NAME.encode() in response.data
    assert b"Log in" in response.data


def test_app_redirects_to_login_when_anonymous(client):
    response = client.get("/app", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_signup_creates_account_and_logs_in(client):
    response = signup(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/app")

    app_response = client.get("/app")
    assert app_response.status_code == 200
    assert b"test@example.com" in app_response.data


def test_signup_rejects_duplicate_email(client):
    signup(client)
    client.post("/logout")
    response = client.post("/signup", data={"email": "test@example.com", "password": "password123"})
    assert response.status_code == 200  # re-renders form with error
    assert b"already exists" in response.data


def test_signup_rejects_short_password(client):
    response = client.post("/signup", data={"email": "short@example.com", "password": "abc"})
    assert response.status_code == 200
    assert b"at least" in response.data


def test_login_with_correct_credentials(client):
    signup(client)
    client.post("/logout")
    response = login(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/app")


def test_login_with_wrong_password_shows_error(client):
    signup(client)
    client.post("/logout")
    response = login(client, password="wrongpassword")
    assert response.status_code == 200
    assert b"Incorrect email or password" in response.data


def test_logout_ends_session(client):
    signup(client)
    client.post("/logout")
    response = client.get("/app", follow_redirects=False)
    assert response.status_code == 302


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"


# ---------------------------------------------------------------------------
# API auth gate
# ---------------------------------------------------------------------------

def test_api_requires_login(client):
    response = client.get("/api/conversations")
    assert response.status_code == 401


def test_chat_without_csrf_token_is_rejected(client):
    signup(client)
    response = client.post("/api/chat", json={"message": "I have a headache"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# /api/chat (non-streaming)
# ---------------------------------------------------------------------------

def test_chat_health_question_gets_real_reply(client):
    token = signup_and_get_csrf(client)
    response = post_json(client, "/api/chat", {"message": "What helps with a headache?"}, token)
    assert response.status_code == 200
    data = response.get_json()
    assert data["topic"] == "health"
    assert data["reply"] == "This is a test reply about your health question."


def test_chat_off_topic_question_gets_default_message(client):
    token = signup_and_get_csrf(client)
    response = post_json(client, "/api/chat", {"message": "What's the latest cricket score?"}, token)
    data = response.get_json()
    assert data["topic"] == "off_topic"
    assert data["reply"] == branding.OFF_TOPIC_MESSAGE


def test_chat_emergency_message_gets_emergency_response(client):
    token = signup_and_get_csrf(client)
    response = post_json(
        client, "/api/chat", {"message": "I am having severe chest pain and can't breathe"}, token
    )
    data = response.get_json()
    assert data["topic"] == "emergency"
    assert data["reply"] == branding.EMERGENCY_MESSAGE


def test_chat_empty_message(client):
    token = signup_and_get_csrf(client)
    response = post_json(client, "/api/chat", {"message": "   "}, token)
    assert response.status_code == 400


def test_chat_too_long(client, app_module):
    token = signup_and_get_csrf(client)
    long_message = "health " * app_module.MAX_MESSAGE_LENGTH
    response = post_json(client, "/api/chat", {"message": long_message}, token)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Persistence + multi-conversation + per-user isolation
# ---------------------------------------------------------------------------

def test_conversation_persists_and_lists(client):
    token = signup_and_get_csrf(client)
    created = post_json(client, "/api/conversations", {}, token)
    conv_id = created.get_json()["id"]

    post_json(client, "/api/chat", {"message": "I have a fever, what should I do?", "conversation_id": conv_id}, token)

    listed = client.get("/api/conversations").get_json()["conversations"]
    matching = next(c for c in listed if c["id"] == conv_id)
    assert matching["title"].startswith("I have a fever")

    fetched = client.get(f"/api/conversations/{conv_id}").get_json()
    assert [t["role"] for t in fetched["history"]] == ["user", "assistant"]


def test_conversations_are_isolated_between_users(client):
    token_a = signup_and_get_csrf(client, email="alice@example.com")
    created = post_json(client, "/api/conversations", {}, token_a)
    conv_id = created.get_json()["id"]

    client.post("/logout")

    token_b = signup_and_get_csrf(client, email="bob@example.com")
    # Bob should not be able to see or fetch Alice's conversation.
    listed = client.get("/api/conversations").get_json()["conversations"]
    assert not any(c["id"] == conv_id for c in listed)

    forbidden = client.get(f"/api/conversations/{conv_id}")
    assert forbidden.status_code == 404

    delete_attempt = client.delete(f"/api/conversations/{conv_id}", headers={"X-CSRF-Token": token_b})
    assert delete_attempt.status_code == 200  # no-op delete, doesn't error, but shouldn't affect Alice's data


def test_session_reset_clears_all_conversations(client):
    token = signup_and_get_csrf(client)
    post_json(client, "/api/chat", {"message": "I have a headache"}, token)
    assert len(client.get("/api/conversations").get_json()["conversations"]) == 1

    reset = post_json(client, "/api/session/reset", {}, token)
    assert reset.status_code == 200
    assert len(client.get("/api/conversations").get_json()["conversations"]) == 0


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def test_chat_stream_health_question_yields_tokens(client):
    token = signup_and_get_csrf(client)
    response = post_json(client, "/api/chat/stream", {"message": "What helps with a headache?"}, token)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "event: meta" in body
    assert "event: token" in body
    assert "event: done" in body


def test_chat_stream_off_topic_does_not_call_gemini(client, app_module):
    token = signup_and_get_csrf(client)
    app_module.client.models.generate_content_stream.reset_mock()
    response = post_json(client, "/api/chat/stream", {"message": "Write a poem about the ocean"}, token)
    body = response.get_data(as_text=True)
    assert branding.OFF_TOPIC_MESSAGE in body
    app_module.client.models.generate_content_stream.assert_not_called()
