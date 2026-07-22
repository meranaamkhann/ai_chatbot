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
    monkeypatch.setenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    monkeypatch.setenv("ENCRYPTION_KEY", "tkGtljKdQPNaeYmt3KQr--k-_13LdA-qc0tUjoeDpLY=")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)

    tmp_dir = tempfile.mkdtemp()
    db_path = Path(tmp_dir) / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    modules = ("app", "db", "conversation_store", "auth", "crypto", "oauth", "mailer", "gemini_client")
    for mod in modules:
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

    for mod in modules:
        sys.modules.pop(mod, None)


@pytest.fixture
def client(app_module):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def get_csrf_from_page(client, path="/app") -> str:
    response = client.get(path)
    body = response.get_data(as_text=True)
    meta_match = re.search(r'name="csrf-token" content="([^"]+)"', body)
    if meta_match:
        return meta_match.group(1)
    form_match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert form_match, f"csrf token not found on {path}"
    return form_match.group(1)


def signup(client, email="test@example.com", password="password123"):
    token = get_csrf_from_page(client, "/signup")
    return client.post(
        "/signup", data={"email": email, "password": password, "csrf_token": token}, follow_redirects=False
    )


def login(client, email="test@example.com", password="password123", next_path=None):
    path = "/login" + (f"?next={next_path}" if next_path else "")
    token = get_csrf_from_page(client, path)
    return client.post(
        path, data={"email": email, "password": password, "csrf_token": token}, follow_redirects=False
    )


def get_csrf_token(client) -> str:
    return get_csrf_from_page(client, "/app")


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


def test_signup_without_csrf_token_is_rejected(client):
    client.get("/signup")  # establish session
    response = client.post("/signup", data={"email": "x@example.com", "password": "password123"})
    assert response.status_code == 200
    assert b"session expired" in response.data


def test_signup_rejects_duplicate_email(client):
    signup(client)
    client.post("/logout")
    response = signup(client, email="test@example.com")
    assert response.status_code == 200
    assert b"already exists" in response.data


def test_signup_rejects_short_password(client):
    response = signup(client, email="short@example.com", password="abc")
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


def test_login_rejects_open_redirect_to_external_site(client):
    """Security regression test: `next=` used to be passed straight to
    redirect(), so a crafted /login?next=https://evil.example.com link
    would send a freshly-authenticated user to an attacker's site."""
    signup(client)
    client.post("/logout")
    response = login(client, next_path="https://evil.example.com")
    assert response.status_code == 302
    assert response.headers["Location"] == "/app"


def test_login_rejects_protocol_relative_redirect(client):
    signup(client)
    client.post("/logout")
    response = login(client, next_path="//evil.example.com")
    assert response.headers["Location"] == "/app"


def test_logout_ends_session(client):
    signup(client)
    client.post("/logout")
    response = client.get("/app", follow_redirects=False)
    assert response.status_code == 302


def test_health_reports_database_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "healthy"
    assert data["database"] is True


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def test_forgot_password_returns_generic_message_for_any_email(client):
    """Same message whether or not the account exists — prevents email enumeration."""
    token = get_csrf_from_page(client, "/forgot-password")
    r1 = client.post("/forgot-password", data={"email": "nonexistent@example.com", "csrf_token": token})
    assert b"If an account exists" in r1.data

    signup(client, email="real@example.com")
    client.post("/logout")
    token2 = get_csrf_from_page(client, "/forgot-password")
    r2 = client.post("/forgot-password", data={"email": "real@example.com", "csrf_token": token2})
    assert b"If an account exists" in r2.data


def test_full_password_reset_flow(client, app_module):
    signup(client, email="reset@example.com", password="oldpassword1")
    client.post("/logout")

    raw_token = app_module.create_password_reset_token("reset@example.com")
    assert raw_token is not None

    reset_page = client.get(f"/reset-password/{raw_token}")
    assert reset_page.status_code == 200
    csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', reset_page.get_data(as_text=True))
    csrf = csrf_match.group(1)

    reset_response = client.post(
        f"/reset-password/{raw_token}",
        data={"password": "newpassword1", "csrf_token": csrf},
    )
    assert reset_response.status_code == 302
    assert reset_response.headers["Location"].endswith("/login")

    # Old password no longer works, new one does.
    assert b"Incorrect" in login(client, email="reset@example.com", password="oldpassword1").data
    assert login(client, email="reset@example.com", password="newpassword1").status_code == 302


def test_reset_token_cannot_be_reused(client, app_module):
    signup(client, email="reuse@example.com")
    client.post("/logout")
    raw_token = app_module.create_password_reset_token("reuse@example.com")

    reset_page = client.get(f"/reset-password/{raw_token}")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', reset_page.get_data(as_text=True)).group(1)
    client.post(f"/reset-password/{raw_token}", data={"password": "firstnewpass1", "csrf_token": csrf})

    second_page = client.get(f"/reset-password/{raw_token}")
    csrf2 = re.search(r'name="csrf_token" value="([^"]+)"', second_page.get_data(as_text=True)).group(1)
    second_attempt = client.post(
        f"/reset-password/{raw_token}", data={"password": "secondnewpass1", "csrf_token": csrf2}
    )
    assert b"invalid or has already been used" in second_attempt.data


def test_invalid_reset_token_is_rejected(client):
    page = client.get("/reset-password/not-a-real-token")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
    response = client.post(
        "/reset-password/not-a-real-token", data={"password": "somenewpassword1", "csrf_token": csrf}
    )
    assert b"invalid or has already been used" in response.data


# ---------------------------------------------------------------------------
# /api/chat (non-streaming)
# ---------------------------------------------------------------------------

def test_chat_without_csrf_token_is_rejected(client):
    signup(client)
    response = client.post("/api/chat", json={"message": "I have a headache"})
    assert response.status_code == 403


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


def test_duplicate_user_turn_is_not_recorded_twice(client, app_module):
    """Regression test: if the frontend's SSE stream fails *before* any
    token arrives, the assistant's reply is never recorded, and the
    frontend falls back to POST /api/chat with the same message and
    conversation_id. That used to record the user's turn a second time.
    This simulates exactly that: user turn recorded, stream dies before
    the assistant reply lands, then the same user message is retried."""
    token = signup_and_get_csrf(client)
    created = post_json(client, "/api/conversations", {}, token)
    conv_id = created.get_json()["id"]

    message = "I have a persistent cough, what should I do?"
    with app_module.app.test_request_context():
        app_module._record_user_turn_once(conv_id, message)
        # Stream "died" here — no assistant reply was ever recorded.
        # The frontend's fallback now retries the same user message:
        app_module._record_user_turn_once(conv_id, message)

    history = client.get(f"/api/conversations/{conv_id}").get_json()["history"]
    user_turns = [h for h in history if h["role"] == "user" and h["content"] == message]
    assert len(user_turns) == 1


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
# Settings: retention + account deletion
# ---------------------------------------------------------------------------

def test_settings_page_loads(client):
    signup(client)
    response = client.get("/settings")
    assert response.status_code == 200
    assert b"Conversation retention" in response.data


def test_settings_updates_retention(client, app_module):
    signup(client)
    token = get_csrf_from_page(client, "/settings")
    response = client.post("/settings", data={"retention_days": "30", "csrf_token": token})
    assert b"saved" in response.data.lower()

    with app_module.app.app_context():
        user_id = app_module.current_user_id()
        assert app_module.store.get_retention_days(user_id) == 30


def test_settings_delete_account_removes_everything(client, app_module):
    signup(client, email="deleteme@example.com")
    csrf_token = get_csrf_token(client)
    post_json(client, "/api/chat", {"message": "I have a headache"}, csrf_token)

    token = get_csrf_from_page(client, "/settings")
    response = client.post("/settings", data={"action": "delete_account", "csrf_token": token}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"] == "/" or response.headers["Location"].endswith("/")

    # Session should be logged out, and the account should no longer exist.
    assert client.get("/app", follow_redirects=False).status_code == 302
    # Signing up again with the same email should now succeed (account was really deleted).
    resignup = signup(client, email="deleteme@example.com")
    assert resignup.status_code == 302


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


# ---------------------------------------------------------------------------
# OAuth (disabled by default in tests — no credentials set)
# ---------------------------------------------------------------------------

def test_oauth_login_route_404s_when_not_configured(client):
    response = client.get("/oauth/google/login")
    assert response.status_code == 404


def test_login_page_hides_oauth_buttons_when_not_configured(client):
    response = client.get("/login")
    assert b"Continue with Google" not in response.data
    assert b"Continue with GitHub" not in response.data
