import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def ctx(monkeypatch):
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setenv("DATABASE_PATH", str(Path(tmp_dir) / "test.db"))
    monkeypatch.setenv("ENCRYPTION_KEY", "tkGtljKdQPNaeYmt3KQr--k-_13LdA-qc0tUjoeDpLY=")

    for mod in ("db", "auth", "crypto"):
        sys.modules.pop(mod, None)

    import db
    import auth

    from flask import Flask

    app = Flask(__name__)
    db.init_db()

    with app.app_context():
        yield auth

    for mod in ("db", "auth", "crypto"):
        sys.modules.pop(mod, None)


# ---------------------------------------------------------------------------
# Open-redirect protection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", ["/app", "/app?prompt=hello", "/settings"])
def test_safe_redirect_targets_are_allowed(ctx, target):
    assert ctx.is_safe_redirect_target(target) is True


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example.com",
        "http://evil.example.com",
        "//evil.example.com",
        "evil.example.com",
        None,
        "",
        "/app\\@evil.example.com",
    ],
)
def test_unsafe_redirect_targets_are_rejected(ctx, target):
    assert ctx.is_safe_redirect_target(target) is False


# ---------------------------------------------------------------------------
# Password reset tokens
# ---------------------------------------------------------------------------

def test_reset_token_for_unknown_email_returns_none(ctx):
    assert ctx.create_password_reset_token("nobody@example.com") is None


def test_reset_token_round_trip(ctx):
    ctx.create_user("reset@example.com", "originalpass1")
    raw_token = ctx.create_password_reset_token("reset@example.com")
    assert raw_token is not None

    ctx.reset_password_with_token(raw_token, "newpassword123")
    # Old password should no longer verify.
    with pytest.raises(ctx.AuthError):
        ctx.verify_login("reset@example.com", "originalpass1")
    # New password should verify.
    user_id = ctx.verify_login("reset@example.com", "newpassword123")
    assert user_id


def test_reset_token_cannot_be_reused(ctx):
    ctx.create_user("reuse@example.com", "originalpass1")
    raw_token = ctx.create_password_reset_token("reuse@example.com")
    ctx.reset_password_with_token(raw_token, "firstnewpass1")

    with pytest.raises(ctx.AuthError, match="already been used"):
        ctx.reset_password_with_token(raw_token, "secondnewpass1")


def test_reset_with_garbage_token_fails(ctx):
    with pytest.raises(ctx.AuthError):
        ctx.reset_password_with_token("not-a-real-token", "newpassword123")


def test_reset_password_too_short_is_rejected(ctx):
    ctx.create_user("short@example.com", "originalpass1")
    raw_token = ctx.create_password_reset_token("short@example.com")
    with pytest.raises(ctx.AuthError, match="at least"):
        ctx.reset_password_with_token(raw_token, "abc")


# ---------------------------------------------------------------------------
# OAuth account linking
# ---------------------------------------------------------------------------

def test_oauth_creates_new_account(ctx):
    user_id = ctx.find_or_create_oauth_user("google", "google-uid-123", "new@example.com")
    assert user_id

    # Second call with the same provider+id returns the same account.
    user_id_2 = ctx.find_or_create_oauth_user("google", "google-uid-123", "new@example.com")
    assert user_id_2 == user_id


def test_oauth_links_to_existing_password_account_by_email(ctx):
    password_user_id = ctx.create_user("shared@example.com", "somepassword1")
    oauth_user_id = ctx.find_or_create_oauth_user("github", "github-uid-456", "shared@example.com")
    assert oauth_user_id == password_user_id


def test_oauth_does_not_confuse_different_providers_with_same_id_string(ctx):
    google_user = ctx.find_or_create_oauth_user("google", "12345", "a@example.com")
    github_user = ctx.find_or_create_oauth_user("github", "12345", "b@example.com")
    assert google_user != github_user
