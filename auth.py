"""
Authentication for Sibbu.

Email + password (hashed with werkzeug's `generate_password_hash`,
PBKDF2-SHA256, already a Flask dependency), plus password reset and
optional Google/GitHub OAuth (see oauth.py — those buttons only appear if
you've configured the provider credentials; nothing breaks if you don't).

Security notes worth stating explicitly rather than leaving implicit:
- `verify_login` always raises the same generic "Incorrect email or
  password" message whether the email doesn't exist or the password is
  wrong — this prevents an attacker from using the login form to
  enumerate which emails have accounts. `create_user`'s "already exists"
  message on signup does leak that one bit of enumeration; that's a
  common, accepted trade-off (a signup form that lies about a duplicate
  account is a worse user experience for a small, real usability cost),
  named here rather than silently glossed over.
- `is_safe_redirect_target` exists specifically to close an open-redirect
  vulnerability: `login_required` used to pass whatever `next=` value
  came from the query string straight into `redirect()`. A crafted link
  like `/app?next=https://evil.example.com` would have sent a user who
  just authenticated straight to an attacker's site. Every `next` value
  is now validated as a same-origin relative path before it's used.

`login_required` covers two call shapes because this app has both page
routes (should redirect to /login) and JSON API routes (should return 401)
behind the same login wall.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_EMAIL_LENGTH = 254  # RFC 5321 max mailbox length
MAX_PASSWORD_LENGTH = 128  # defensive cap — not a security requirement, just sane input hygiene
RESET_TOKEN_LIFETIME = timedelta(hours=1)


class AuthError(ValueError):
    """Raised for user-facing signup/login validation failures."""


def is_safe_redirect_target(target: str | None) -> bool:
    """True only for a same-site relative path like '/app' or '/app?x=1'.

    Rejects absolute URLs (http://..., https://...), protocol-relative
    URLs (//evil.example.com — browsers treat this as a full redirect to
    a different host), and anything not starting with a single '/'.
    """
    if not target:
        return False
    if not target.startswith("/"):
        return False
    if target.startswith("//"):
        return False
    if "\\" in target:  # some browsers normalize backslashes to forward slashes
        return False
    return True


def validate_signup(email: str, password: str) -> None:
    if not email or len(email) > MAX_EMAIL_LENGTH or not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError(f"Password must be under {MAX_PASSWORD_LENGTH} characters.")


def create_user(email: str, password: str) -> str:
    email = email.strip().lower()
    validate_signup(email, password)

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        raise AuthError("An account with that email already exists.")

    user_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
        (user_id, email, generate_password_hash(password)),
    )
    db.commit()
    return user_id


def verify_login(email: str, password: str) -> str:
    email = (email or "").strip().lower()
    db = get_db()
    row = db.execute(
        "SELECT id, password_hash FROM users WHERE email = ?", (email,)
    ).fetchone()
    if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], password or ""):
        raise AuthError("Incorrect email or password.")
    return row["id"]


def log_in_user(user_id: str) -> None:
    session.clear()
    session["user_id"] = user_id
    session.permanent = True


def log_out_user() -> None:
    session.clear()


def current_user_id() -> str | None:
    return session.get("user_id")


def _session_user_still_exists(user_id: str) -> bool:
    """True if this user_id actually has a row in the database.

    This matters specifically because of how free-tier hosting behaves:
    Render's free web service disk is ephemeral, so a redeploy or an
    idle-spin-down wipes the SQLite file, but a visitor's browser still
    holds a perfectly valid, unexpired session cookie pointing at a
    user_id that no longer exists anywhere. Without this check, every
    route that looks up `SELECT ... WHERE id = ?` and assumes a row
    exists crashes with an unhandled 500 the moment that happens — which
    is exactly the bug this closes, verified against the exact crash
    (`db.execute(...).fetchone()["email"]` on a None row) seen in
    production and reproduced locally after a DB reset.
    """
    row = get_db().execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
    return row is not None


def login_required(view):
    """Use on page routes — redirects anonymous visitors to /login.

    Also self-heals a stale session: if the cookie's user_id has no
    matching row (deleted account, or the DB was reset/redeployed under
    it), the session is cleared and the visitor is sent to log in again,
    rather than the route crashing on a lookup that assumes the user
    still exists.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = current_user_id()
        if not user_id or not _session_user_still_exists(user_id):
            log_out_user()
            next_target = request.full_path if request.query_string else request.path
            if not is_safe_redirect_target(next_target):
                next_target = None
            return redirect(url_for("login_page", next=next_target))
        return view(*args, **kwargs)

    return wrapped


def api_login_required(view):
    """Use on JSON API routes — returns 401 instead of redirecting.

    Same self-healing as login_required: a stale session pointing at a
    since-deleted or since-wiped user is treated as logged out, not as a
    crash.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = current_user_id()
        if not user_id or not _session_user_still_exists(user_id):
            log_out_user()
            return jsonify({"error": "Please log in to continue."}), 401
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def _hash_token(raw_token: str) -> str:
    # Reset tokens are stored hashed (SHA-256 via werkzeug's PBKDF2 would be
    # overkill and slow for a high-entropy random token that's never
    # user-chosen) so that read access to the DB alone doesn't hand out
    # working reset links.
    import hashlib

    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_password_reset_token(email: str) -> str | None:
    """Returns a raw token to email to the user, or None if no account
    matches — the caller should behave identically either way (always
    say "if an account exists, we've sent a link") to avoid leaking
    which emails have accounts via the forgot-password form."""
    email = (email or "").strip().lower()
    db = get_db()
    row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        return None

    raw_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + RESET_TOKEN_LIFETIME).isoformat()
    db.execute(
        "INSERT INTO password_reset_tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash_token(raw_token), row["id"], expires_at),
    )
    db.commit()
    return raw_token


def reset_password_with_token(raw_token: str, new_password: str) -> None:
    if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(new_password) > MAX_PASSWORD_LENGTH:
        raise AuthError(f"Password must be under {MAX_PASSWORD_LENGTH} characters.")

    db = get_db()
    token_hash = _hash_token(raw_token)
    row = db.execute(
        "SELECT user_id, expires_at, used FROM password_reset_tokens WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()

    if not row or row["used"]:
        raise AuthError("This reset link is invalid or has already been used.")
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        raise AuthError("This reset link has expired. Request a new one.")

    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), row["user_id"]),
    )
    db.execute("UPDATE password_reset_tokens SET used = 1 WHERE token_hash = ?", (token_hash,))
    db.commit()


# ---------------------------------------------------------------------------
# OAuth (Google / GitHub) — see oauth.py for the provider-facing flow
# ---------------------------------------------------------------------------

def find_or_create_oauth_user(provider: str, oauth_id: str, email: str) -> str:
    """Look up an existing account for this OAuth identity, or create one.

    If an email/password account already exists with the same email, that
    account is linked (oauth_provider/oauth_id set on it) rather than
    creating a duplicate — so someone who signed up with a password can
    later also log in with Google/GitHub using the same address.
    """
    email = (email or "").strip().lower()
    db = get_db()

    by_oauth = db.execute(
        "SELECT id FROM users WHERE oauth_provider = ? AND oauth_id = ?", (provider, oauth_id)
    ).fetchone()
    if by_oauth:
        return by_oauth["id"]

    by_email = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if by_email:
        db.execute(
            "UPDATE users SET oauth_provider = ?, oauth_id = ? WHERE id = ?",
            (provider, oauth_id, by_email["id"]),
        )
        db.commit()
        return by_email["id"]

    user_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO users (id, email, password_hash, oauth_provider, oauth_id) VALUES (?, ?, ?, ?, ?)",
        (user_id, email, None, provider, oauth_id),
    )
    db.commit()
    return user_id
