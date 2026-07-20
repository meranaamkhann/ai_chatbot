import re
import sys
from unittest.mock import MagicMock, patch

import pytest

import branding


@pytest.fixture
def app_module(monkeypatch):
    """Import app.py fresh with required env vars set and the Gemini client mocked out."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-flash-lite-latest")

    sys.modules.pop("app", None)

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

    sys.modules.pop("app", None)


@pytest.fixture
def client(app_module):
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def get_csrf_token(client) -> str:
    response = client.get("/app")
    match = re.search(r'name="csrf-token" content="([^"]+)"', response.get_data(as_text=True))
    assert match, "csrf token meta tag not found on /app"
    return match.group(1)


def post_json(client, path, payload, csrf_token=None):
    headers = {"X-CSRF-Token": csrf_token} if csrf_token else {}
    return client.post(path, json=payload, headers=headers)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def test_landing_page_loads(client, app_module):
    response = client.get("/")
    assert response.status_code == 200
    assert app_module.branding.BRAND_NAME.encode() in response.data


def test_chat_app_page_loads_with_csrf_token(client):
    token = get_csrf_token(client)
    assert len(token) >= 32


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"


def test_404(client):
    response = client.get("/no-such-route")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# CSRF enforcement
# ---------------------------------------------------------------------------

def test_chat_without_csrf_token_is_rejected(client):
    get_csrf_token(client)  # establish session
    response = client.post("/api/chat", json={"message": "I have a headache"})
    assert response.status_code == 403


def test_chat_with_wrong_csrf_token_is_rejected(client):
    get_csrf_token(client)
    response = post_json(client, "/api/chat", {"message": "I have a headache"}, csrf_token="wrong-token")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# /api/chat (non-streaming)
# ---------------------------------------------------------------------------

def test_chat_health_question_gets_real_reply(client):
    token = get_csrf_token(client)
    response = post_json(client, "/api/chat", {"message": "What helps with a headache?"}, token)
    assert response.status_code == 200
    data = response.get_json()
    assert data["topic"] == "health"
    assert data["reply"] == "This is a test reply about your health question."
    assert "conversation_id" in data


def test_chat_off_topic_question_gets_default_message(client):
    token = get_csrf_token(client)
    response = post_json(client, "/api/chat", {"message": "What's the latest cricket score?"}, token)
    assert response.status_code == 200
    data = response.get_json()
    assert data["topic"] == "off_topic"
    assert data["reply"] == branding.OFF_TOPIC_MESSAGE


def test_chat_greeting_gets_friendly_reply_not_rejection(client):
    token = get_csrf_token(client)
    response = post_json(client, "/api/chat", {"message": "Hello"}, token)
    data = response.get_json()
    assert data["topic"] == "greeting"
    assert data["reply"] == branding.GREETING_REPLY


def test_chat_emergency_message_gets_emergency_response(client):
    token = get_csrf_token(client)
    response = post_json(
        client, "/api/chat", {"message": "I am having severe chest pain and can't breathe"}, token
    )
    data = response.get_json()
    assert data["topic"] == "emergency"
    assert data["reply"] == branding.EMERGENCY_MESSAGE


def test_chat_empty_message(client):
    token = get_csrf_token(client)
    response = post_json(client, "/api/chat", {"message": "   "}, token)
    assert response.status_code == 400


def test_chat_missing_message(client):
    token = get_csrf_token(client)
    response = post_json(client, "/api/chat", {}, token)
    assert response.status_code == 400


def test_chat_too_long(client, app_module):
    token = get_csrf_token(client)
    long_message = "health " * app_module.MAX_MESSAGE_LENGTH
    response = post_json(client, "/api/chat", {"message": long_message}, token)
    assert response.status_code == 400


def test_chat_non_json(client):
    token = get_csrf_token(client)
    response = client.post(
        "/api/chat", data="not json", content_type="text/plain", headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 415


def test_chat_api_error_returns_503(client, app_module):
    from google.genai import errors as genai_errors

    token = get_csrf_token(client)
    app_module.client.models.generate_content.side_effect = genai_errors.APIError(500, {"message": "boom"})
    response = post_json(client, "/api/chat", {"message": "I have a headache, what should I do?"}, token)
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Multi-conversation API
# ---------------------------------------------------------------------------

def test_conversation_lifecycle(client):
    token = get_csrf_token(client)

    created = post_json(client, "/api/conversations", {}, token)
    assert created.status_code == 201
    conv_id = created.get_json()["id"]

    chat_resp = post_json(
        client, "/api/chat", {"message": "I have a fever, what should I do?", "conversation_id": conv_id}, token
    )
    assert chat_resp.get_json()["conversation_id"] == conv_id

    listed = client.get("/api/conversations").get_json()["conversations"]
    assert any(c["id"] == conv_id for c in listed)
    # Title should have been derived from the first user message.
    matching = next(c for c in listed if c["id"] == conv_id)
    assert matching["title"].startswith("I have a fever")

    fetched = client.get(f"/api/conversations/{conv_id}").get_json()
    roles = [turn["role"] for turn in fetched["history"]]
    assert roles == ["user", "assistant"]

    deleted = client.delete(f"/api/conversations/{conv_id}", headers={"X-CSRF-Token": token})
    assert deleted.status_code == 200

    listed_after = client.get("/api/conversations").get_json()["conversations"]
    assert not any(c["id"] == conv_id for c in listed_after)


def test_session_reset_clears_all_conversations(client):
    token = get_csrf_token(client)
    post_json(client, "/api/chat", {"message": "I have a headache"}, token)
    assert len(client.get("/api/conversations").get_json()["conversations"]) == 1

    reset = post_json(client, "/api/session/reset", {}, token)
    assert reset.status_code == 200
    assert len(client.get("/api/conversations").get_json()["conversations"]) == 0


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def test_chat_stream_health_question_yields_tokens(client):
    token = get_csrf_token(client)
    response = post_json(client, "/api/chat/stream", {"message": "What helps with a headache?"}, token)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"

    body = response.get_data(as_text=True)
    assert "event: meta" in body
    assert "event: token" in body
    assert "event: done" in body
    assert "This is a test reply" in body


def test_chat_stream_off_topic_does_not_call_gemini(client, app_module):
    token = get_csrf_token(client)
    app_module.client.models.generate_content_stream.reset_mock()
    response = post_json(client, "/api/chat/stream", {"message": "Write a poem about the ocean"}, token)
    body = response.get_data(as_text=True)
    assert branding.OFF_TOPIC_MESSAGE in body
    app_module.client.models.generate_content_stream.assert_not_called()
