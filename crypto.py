"""
Field-level encryption at rest for Sibbu.

Why application-level encryption and not SQLCipher (encrypted SQLite):
SQLCipher needs a compiled SQLite extension that isn't part of the Python
standard library and isn't reliably available on every free hosting
tier's build image without extra work. Fernet (from the `cryptography`
package — already an existing transitive dependency of `google-genai`,
now pinned explicitly since we depend on it directly) gets the actual
security property that matters here — message content unreadable to
anyone with just filesystem/DB access — with a pure-Python dependency
that installs anywhere `pip install` works.

What's encrypted: message content and conversation titles (both derived
from what the user actually said, which is the sensitive part). What's
NOT encrypted: email addresses (need to stay queryable for login) and
password hashes (already irreversible via PBKDF2, encrypting a hash adds
no security property). Timestamps and role ("user"/"assistant") are
metadata, not health content, so they're left as plain columns — encrypting
them would only make debugging harder for no real privacy gain.

Key management: ENCRYPTION_KEY is a required env var (a Fernet key — 32
url-safe base64-encoded bytes). Generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Losing this key means losing the ability to read any existing message —
there is deliberately no recovery path, because a recoverable key is a
weaker guarantee than an unrecoverable one. Rotating it requires
decrypting everything with the old key and re-encrypting with the new one
before swapping the env var; that migration script doesn't exist yet and
is a real gap, not something quietly handled here.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

# Defensive, not just redundant with app.py's own load_dotenv() call: this
# module raises immediately at import time if the key is missing, so it
# can't rely on whatever imports it having already loaded .env in the
# right order. load_dotenv() is safe to call more than once.
load_dotenv()

_ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

if not _ENCRYPTION_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY is not set. Generate one with:\n"
        '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
        "and add it to your .env file. This encrypts message content and "
        "conversation titles at rest — see crypto.py for why it's required, "
        "not optional."
    )

_fernet = Fernet(_ENCRYPTION_KEY.encode())


def encrypt_text(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_text(ciphertext: str) -> str:
    try:
        return _fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Deliberately visible rather than silently returning garbage or an
        # empty string — a decrypt failure means either the wrong key is
        # configured or the data predates encryption being turned on (see
        # the migration note in AUDIT.md). Either way, papering over it
        # would hide a real data-integrity problem.
        return "[This message can't be decrypted — it may predate encryption being enabled, or ENCRYPTION_KEY has changed.]"
