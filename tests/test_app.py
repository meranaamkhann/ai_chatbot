import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


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
        mock_response.text = "This is a test reply."
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


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"HealthBot" in response.data


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "healthy"


def test_chat_success(client, app_module):
    response = client.post("/chat", json={"message": "Hello"})
    assert response.status_code == 200
    assert response.get_json()["reply"] == "This is a test reply."


def test_chat_empty_message(client):
    response = client.post("/chat", json={"message": "   "})
    assert response.status_code == 400


def test_chat_missing_message(client):
    response = client.post("/chat", json={})
    assert response.status_code == 400


def test_chat_too_long(client, app_module):
    long_message = "a" * (app_module.MAX_MESSAGE_LENGTH + 1)
    response = client.post("/chat", json={"message": long_message})
    assert response.status_code == 400


def test_chat_non_json(client):
    response = client.post("/chat", data="not json", content_type="text/plain")
    assert response.status_code == 415


def test_chat_history_persists_across_requests(client, app_module):
    client.post("/chat", json={"message": "first message"})
    client.post("/chat", json={"message": "second message"})

    # Find the session's chat record and confirm history accumulated.
    assert len(app_module._chat_store) == 1
    record = next(iter(app_module._chat_store.values()))
    user_messages = [m["content"] for m in record["history"] if m["role"] == "user"]
    assert "first message" in user_messages
    assert "second message" in user_messages


def test_clear_history(client, app_module):
    client.post("/chat", json={"message": "hello"})
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
    response = client.post("/chat", json={"message": "hello"})
    assert response.status_code == 503
