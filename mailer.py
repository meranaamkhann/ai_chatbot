"""
Email sending for Sibbu — currently just password reset links.

Why plain SMTP and not a transactional email API (SendGrid, Postmark,
etc.): those all require their own account signup and most gate real
sending behind a paid plan or a small free quota. `smtplib` is in the
Python standard library, and Gmail (or Outlook, or any other provider)
gives every account a free "app password" for exactly this use case —
zero new accounts, zero new cost, on top of infrastructure you likely
already have.

If SMTP_* env vars aren't set, `send_email` logs the message instead of
sending it and returns False. That's a deliberate fallback for local
development — you can test the reset flow end-to-end by reading the
token out of the log line rather than needing real email configured.
In production, missing SMTP config means reset emails silently don't
arrive — that's a real gap, not hidden: check `/health` context or logs
if reset emails aren't showing up.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("sibbu.mail")

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME or "no-reply@example.com")


def send_email(to_address: str, subject: str, body: str) -> bool:
    if not (SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD):
        logger.warning(
            "SMTP not configured — email not sent. Would have sent to %s: %s\n%s",
            to_address, subject, body,
        )
        return False

    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = to_address

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_address], message.as_string())
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_address)
        return False


def send_password_reset_email(to_address: str, reset_url: str, brand_name: str) -> bool:
    subject = f"Reset your {brand_name} password"
    body = (
        f"Someone (hopefully you) requested a password reset for your {brand_name} account.\n\n"
        f"Reset your password here (this link expires in 1 hour):\n{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email — your password won't change."
    )
    return send_email(to_address, subject, body)
