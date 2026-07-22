"""
Optional Google/GitHub OAuth login for Sibbu, via Authlib.

Deliberately opt-in per provider: `google_enabled()` / `github_enabled()`
are true only if that provider's CLIENT_ID/CLIENT_SECRET env vars are
set. The login/signup pages only render a provider's button when it's
enabled, so an installation with zero OAuth configured behaves exactly
like the email/password-only version — nothing breaks, nothing looks
half-finished.

Setting these up requires creating an OAuth app in each provider's own
console — that's not something that can be done from here, only
documented:

Google (Google Cloud Console → APIs & Services → Credentials):
  1. Create an OAuth 2.0 Client ID (type: Web application)
  2. Authorized redirect URI: <your-domain>/oauth/google/callback
     (e.g. http://127.0.0.1:5000/oauth/google/callback for local dev,
     https://your-app.onrender.com/oauth/google/callback in production)
  3. Copy the Client ID and Client Secret into GOOGLE_CLIENT_ID /
     GOOGLE_CLIENT_SECRET in your .env

GitHub (github.com/settings/developers → OAuth Apps → New OAuth App):
  1. Authorization callback URL: <your-domain>/oauth/github/callback
  2. Copy the Client ID and generate a Client Secret into
     GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET in your .env
"""

from __future__ import annotations

import os

from authlib.integrations.flask_client import OAuth

oauth = OAuth()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")


def google_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def github_enabled() -> bool:
    return bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)


def init_oauth(app) -> None:
    oauth.init_app(app)

    if google_enabled():
        oauth.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )

    if github_enabled():
        oauth.register(
            name="github",
            client_id=GITHUB_CLIENT_ID,
            client_secret=GITHUB_CLIENT_SECRET,
            access_token_url="https://github.com/login/oauth/access_token",
            authorize_url="https://github.com/login/oauth/authorize",
            api_base_url="https://api.github.com/",
            client_kwargs={"scope": "read:user user:email"},
        )


def fetch_google_identity(token: dict) -> tuple[str, str]:
    """Returns (oauth_id, email) from a completed Google OAuth token."""
    userinfo = token.get("userinfo") or oauth.google.userinfo(token=token)
    return userinfo["sub"], userinfo["email"]


def fetch_github_identity(token: dict) -> tuple[str, str]:
    """Returns (oauth_id, email) from a completed GitHub OAuth token.

    GitHub's primary /user endpoint doesn't always include email (depends
    on the user's privacy setting), so we fall back to the dedicated
    emails endpoint and pick the primary, verified address.
    """
    profile = oauth.github.get("user", token=token).json()
    email = profile.get("email")

    if not email:
        emails = oauth.github.get("user/emails", token=token).json()
        primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
        email = primary["email"] if primary else (emails[0]["email"] if emails else None)

    if not email:
        raise ValueError("Could not read an email address from your GitHub account.")

    return str(profile["id"]), email.strip().lower()
