import sys

import pytest


def _fresh_mailer(monkeypatch, **env):
    for key in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("mailer", None)
    import mailer

    return mailer


def test_send_email_returns_false_when_smtp_not_configured(monkeypatch):
    mailer = _fresh_mailer(monkeypatch)
    result = mailer.send_email("someone@example.com", "Subject", "Body")
    assert result is False


def test_send_password_reset_email_degrades_gracefully_without_smtp(monkeypatch):
    mailer = _fresh_mailer(monkeypatch)
    result = mailer.send_password_reset_email("someone@example.com", "https://example.com/reset/abc", "Sibbu")
    assert result is False  # logged, not sent — but doesn't raise
