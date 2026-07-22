import sys

import pytest


def _fresh_oauth(monkeypatch, **env):
    for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    sys.modules.pop("oauth", None)
    import oauth

    return oauth


def test_google_disabled_when_no_credentials(monkeypatch):
    oauth = _fresh_oauth(monkeypatch)
    assert oauth.google_enabled() is False


def test_google_disabled_when_only_one_credential_set(monkeypatch):
    oauth = _fresh_oauth(monkeypatch, GOOGLE_CLIENT_ID="abc")
    assert oauth.google_enabled() is False


def test_google_enabled_when_both_credentials_set(monkeypatch):
    oauth = _fresh_oauth(monkeypatch, GOOGLE_CLIENT_ID="abc", GOOGLE_CLIENT_SECRET="def")
    assert oauth.google_enabled() is True


def test_github_enabled_when_both_credentials_set(monkeypatch):
    oauth = _fresh_oauth(monkeypatch, GITHUB_CLIENT_ID="abc", GITHUB_CLIENT_SECRET="def")
    assert oauth.github_enabled() is True


def test_providers_are_independent(monkeypatch):
    oauth = _fresh_oauth(monkeypatch, GOOGLE_CLIENT_ID="abc", GOOGLE_CLIENT_SECRET="def")
    assert oauth.google_enabled() is True
    assert oauth.github_enabled() is False
