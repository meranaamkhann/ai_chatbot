"""
Authentication for Sibbu.

Deliberately minimal: email + password, hashed with werkzeug's
`generate_password_hash` (PBKDF2-SHA256, already a Flask dependency — no
new package for hashing), sessions via Flask's signed cookie (already in
use for CSRF tokens). No OAuth, no email verification, no password reset
flow — those are real gaps for a production auth system, called out
explicitly in AUDIT.md rather than silently left out.

`login_required` covers two call shapes because this app has both page
routes (should redirect to /login) and JSON API routes (should return 401)
behind the same login wall.
"""

from __future__ import annotations

import re
import uuid
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


class AuthError(ValueError):
    """Raised for user-facing signup/login validation failures."""


def validate_signup(email: str, password: str) -> None:
    if not email or not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


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
    row = db.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    if not row or not check_password_hash(row["password_hash"], password or ""):
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


def login_required(view):
    """Use on page routes — redirects anonymous visitors to /login."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            next_target = request.full_path if request.query_string else request.path
            return redirect(url_for("login_page", next=next_target))
        return view(*args, **kwargs)

    return wrapped


def api_login_required(view):
    """Use on JSON API routes — returns 401 instead of redirecting."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return jsonify({"error": "Please log in to continue."}), 401
        return view(*args, **kwargs)

    return wrapped
