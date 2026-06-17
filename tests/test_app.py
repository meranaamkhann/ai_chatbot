import sys
from unittest.mock import MagicMock, patch

import pytest

import branding


@pytest.fixture
def app_module(monkeypatch):
    """Import app.py fresh with required env vars set and the Gemini client mocked out."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")

    sys.modules.pop("app", None)

    with patch("google.genai.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "This is a test reply about your health question."
        mock_client.models.generate_content.return_value = mock_response
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


def test_home_page_loads(client, app_module):
    response = client.get("/")
    assert response.status_code == 200
    assert app_module.branding.BRAND_NAME.encode() in response.data


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"


def test_chat_health_question_gets_real_reply(client):
    response = client.post("/chat", json={"message": "What helps with a headache?"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["topic"] == "health"
    assert data["reply"] == "This is a test reply about your health question."


def test_chat_off_topic_question_gets_default_message(client):
    response = client.post("/chat", json={"message": "What's the latest cricket score?"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["topic"] == "off_topic"
    assert data["reply"] == branding.OFF_TOPIC_MESSAGE


def test_chat_greeting_gets_friendly_reply_not_rejection(client):
    response = client.post("/chat", json={"message": "Hello"})
    assert response.status_code == 200
    data = response.get_json()
    assert data["topic"] == "greeting"
    assert data["reply"] == branding.GREETING_REPLY
    assert "Sorry, I can only answer" not in data["reply"]


def test_chat_greeting_does_not_call_gemini(client, app_module):
    app_module.client.models.generate_content.reset_mock()
    client.post("/chat", json={"message": "hi there"})
    app_module.client.models.generate_content.assert_not_called()


def test_chat_off_topic_does_not_call_gemini_for_main_reply(client, app_module):
    app_module.client.models.generate_content.reset_mock()
    client.post("/chat", json={"message": "Write a poem about the ocean"})
    # The off-topic path should short-circuit before the main conversational
    # generate_content call (only the classifier, if invoked, would call it -
    # but this message hits the keyword pass directly, so no calls at all).
    app_module.client.models.generate_content.assert_not_called()


def test_chat_emergency_message_gets_emergency_response(client):
    response = client.post(
        "/chat", json={"message": "I am having severe chest pain and can't breathe"}
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["topic"] == "emergency"
    assert data["reply"] == branding.EMERGENCY_MESSAGE


def test_chat_empty_message(client):
    response = client.post("/chat", json={"message": "   "})
    assert response.status_code == 400


def test_chat_missing_message(client):
    response = client.post("/chat", json={})
    assert response.status_code == 400


def test_chat_too_long(client, app_module):
    long_message = "health " * (app_module.MAX_MESSAGE_LENGTH)
    response = client.post("/chat", json={"message": long_message})
    assert response.status_code == 400


def test_chat_non_json(client):
    response = client.post("/chat", data="not json", content_type="text/plain")
    assert response.status_code == 415


def test_chat_history_persists_across_requests(client, app_module):
    client.post("/chat", json={"message": "I have a fever, what should I do?"})
    client.post("/chat", json={"message": "How long does a fever usually last?"})

    assert len(app_module._chat_store) == 1
    record = next(iter(app_module._chat_store.values()))
    user_messages = [m["content"] for m in record["history"] if m["role"] == "user"]
    assert "I have a fever, what should I do?" in user_messages
    assert "How long does a fever usually last?" in user_messages


def test_off_topic_replies_are_also_recorded_in_history(client, app_module):
    client.post("/chat", json={"message": "What's the bitcoin price today?"})

    record = next(iter(app_module._chat_store.values()))
    assistant_messages = [m["content"] for m in record["history"] if m["role"] == "assistant"]
    assert branding.OFF_TOPIC_MESSAGE in assistant_messages


def test_clear_history(client, app_module):
    client.post("/chat", json={"message": "I have a headache"})
    assert len(app_module._chat_store) == 1

    response = client.post("/clear_history")
    assert response.status_code == 200
    assert len(app_module._chat_store) == 0


def test_404(client):
    response = client.get("/no-such-route")
    assert response.status_code == 404


def test_chat_api_error_returns_503(client, app_module):
    from google.genai import errors as genai_errors

    app_module.client.models.generate_content.side_effect = genai_errors.APIError(
        500, {"message": "boom"}
    )
    response = client.post("/chat", json={"message": "I have a headache, what should I do?"})
    assert response.status_code == 503
