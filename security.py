"""
Lightweight CSRF protection and security headers for Sibbu.

The original app had no CSRF defense at all beyond `SameSite=Lax` cookies.
Lax blocks cross-site *form* POSTs but does not block a cross-site page
from issuing a same-site-cookie-carrying `fetch()` POST in all browsers,
and relying solely on cookie SameSite policy as your only CSRF defense is
fragile. This module adds a standard double-submit token: the server
hands the page a per-session token when it renders the chat UI, and every
state-changing request must echo it back in a custom header. A page on
another origin can't read that token (no CORS access to the response),
so it can't forge the header.

No flask-wtf dependency: the whole project is one small extra file
because the actual mechanism is ~15 lines.
"""

import hmac
import secrets

from flask import Request, Response, session

CSRF_HEADER_NAME = "X-CSRF-Token"


def get_or_create_csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def csrf_token_is_valid(request: Request) -> bool:
    session_token = session.get("csrf_token")
    header_token = request.headers.get(CSRF_HEADER_NAME, "")
    if not session_token or not header_token:
        return False
    return hmac.compare_digest(session_token, header_token)


def apply_security_headers(response: Response) -> Response:
    """Defense-in-depth headers. Cheap, zero-dependency, and unrelated to CSRF."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # No external script/style/font/image sources anywhere in the app, so
    # the CSP can stay tight without needing to allowlist a CDN.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response
