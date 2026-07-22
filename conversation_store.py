"""
Conversation store for Sibbu — SQLite-backed, keyed by user account,
with message content and titles encrypted at rest (crypto.py), optional
rolling summarization for long conversations, and configurable
per-user retention.

Summarization: the LLM prompt sent to Gemini is capped at a small window
of recent messages, same as before — but instead of just silently
dropping everything older than that window (losing context ChatGPT-style
products preserve via summarization), older turns are folded into a
short running summary via one extra Gemini call, triggered only when the
window fills up (not on every turn). `conversations.summary_through_id`
tracks which message the summary already covers, so the same turns are
never re-summarized. The UI always shows the *full* raw history — only
what gets sent to the model is condensed.

Retention: `users.retention_days` is nullable (NULL = keep forever, the
default). `purge_expired_for_user` deletes conversations whose
`updated_at` is older than that window. There's no background
scheduler on a free single-instance deployment, so this runs
opportunistically — see app.py, which calls it once per /app page load,
throttled so it's at most a real query per session, not per request.
That's a real limitation of $0 hosting, not hidden: a conversation past
its retention window might survive a little past the exact cutoff if the
user doesn't open the app again, rather than being purged the instant it
expires.
"""

from __future__ import annotations

import uuid

from crypto import decrypt_text, encrypt_text
from db import get_db

MAX_CONVERSATIONS_PER_USER = 50
MAX_HISTORY_MESSAGES = 20        # hard cap on what's ever loaded from DB per conversation
RECENT_WINDOW_FOR_PROMPT = 8     # how many raw recent turns accompany the summary in a prompt
SUMMARY_TRIGGER_COUNT = 16       # summarize once unsummarized history reaches this many messages
TITLE_MAX_LEN = 48
DEFAULT_TITLE = "New conversation"


def new_conversation(user_id: str) -> dict:
    db = get_db()
    conv_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
        (conv_id, user_id, encrypt_text(DEFAULT_TITLE)),
    )
    _evict_oldest_if_over_cap(db, user_id)
    db.commit()
    return {"id": conv_id, "title": DEFAULT_TITLE}


def _evict_oldest_if_over_cap(db, user_id: str) -> None:
    count = db.execute(
        "SELECT COUNT(*) AS n FROM conversations WHERE user_id = ?", (user_id,)
    ).fetchone()["n"]
    if count > MAX_CONVERSATIONS_PER_USER:
        oldest = db.execute(
            "SELECT id FROM conversations WHERE user_id = ? ORDER BY updated_at ASC, rowid ASC LIMIT ?",
            (user_id, count - MAX_CONVERSATIONS_PER_USER),
        ).fetchall()
        db.executemany("DELETE FROM conversations WHERE id = ?", [(row["id"],) for row in oldest])


def list_conversations(user_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT id, title, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC, rowid DESC",
        (user_id,),
    ).fetchall()
    return [{"id": row["id"], "title": decrypt_text(row["title"]), "updated_at": row["updated_at"]} for row in rows]


def owns_conversation(user_id: str, conv_id: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
    ).fetchone()
    return row is not None


def get_history(conv_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """Full (or last `limit`) raw message history — what the UI renders."""
    db = get_db()
    rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conv_id,),
    ).fetchall()
    trimmed = rows[-limit:] if limit else rows
    return [{"role": row["role"], "content": decrypt_text(row["content"])} for row in trimmed]


def last_message_matches(conv_id: str, role: str, content: str) -> bool:
    """True if the most recent message in this conversation already has
    this exact role+content — used to make recording a turn idempotent
    against the streaming-failed-then-fell-back-to-classic-endpoint case,
    where the same user message could otherwise be double-recorded."""
    db = get_db()
    row = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
        (conv_id,),
    ).fetchone()
    if not row:
        return False
    return row["role"] == role and decrypt_text(row["content"]) == content


def delete_conversation(user_id: str, conv_id: str) -> None:
    db = get_db()
    db.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))
    db.commit()


def clear_user_conversations(user_id: str) -> None:
    db = get_db()
    db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
    db.commit()


def set_lang_if_unset(conv_id: str, lang: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE conversations SET lang = ? WHERE id = ? AND lang IS NULL",
        (lang, conv_id),
    )
    db.commit()


def record_turn(conv_id: str, role: str, content: str) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
        (conv_id, role, encrypt_text(content)),
    )

    if role == "user":
        row = db.execute("SELECT title FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if row and decrypt_text(row["title"]) == DEFAULT_TITLE:
            title = (content[:TITLE_MAX_LEN] + "…") if len(content) > TITLE_MAX_LEN else content
            db.execute("UPDATE conversations SET title = ? WHERE id = ?", (encrypt_text(title), conv_id))

    db.execute("UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (conv_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Rolling summarization
# ---------------------------------------------------------------------------

def get_prompt_context(conv_id: str) -> tuple[str | None, list[dict]]:
    """Returns (summary_or_None, recent_messages) for building an LLM prompt.

    `recent_messages` only includes turns after `summary_through_id` (or
    the last RECENT_WINDOW_FOR_PROMPT turns if nothing's been summarized
    yet) — this is what keeps the prompt small regardless of how long the
    conversation has actually grown in the database.
    """
    db = get_db()
    conv = db.execute(
        "SELECT summary, summary_through_id FROM conversations WHERE id = ?", (conv_id,)
    ).fetchone()
    summary = decrypt_text(conv["summary"]) if conv and conv["summary"] else None
    through_id = conv["summary_through_id"] if conv else None

    if through_id:
        rows = db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id ASC",
            (conv_id, through_id),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conv_id,),
        ).fetchall()
        rows = rows[-RECENT_WINDOW_FOR_PROMPT:] if not summary else rows

    recent = [{"role": r["role"], "content": decrypt_text(r["content"])} for r in rows]
    return summary, recent


def needs_summarization(conv_id: str) -> bool:
    db = get_db()
    conv = db.execute("SELECT summary_through_id FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    through_id = conv["summary_through_id"] if conv else None

    if through_id:
        count = db.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND id > ?", (conv_id, through_id)
        ).fetchone()["n"]
    else:
        count = db.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?", (conv_id,)
        ).fetchone()["n"]

    return count >= SUMMARY_TRIGGER_COUNT


def messages_to_fold_into_summary(conv_id: str) -> tuple[list[dict], int | None]:
    """Returns (messages_to_summarize, up_to_message_id) — everything
    except the most recent RECENT_WINDOW_FOR_PROMPT turns, which stay raw."""
    db = get_db()
    conv = db.execute("SELECT summary_through_id FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    through_id = conv["summary_through_id"] if conv else None

    if through_id:
        rows = db.execute(
            "SELECT id, role, content FROM messages WHERE conversation_id = ? AND id > ? ORDER BY id ASC",
            (conv_id, through_id),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conv_id,),
        ).fetchall()

    if len(rows) <= RECENT_WINDOW_FOR_PROMPT:
        return [], None

    to_fold = rows[:-RECENT_WINDOW_FOR_PROMPT]
    up_to_id = to_fold[-1]["id"]
    return [{"role": r["role"], "content": decrypt_text(r["content"])} for r in to_fold], up_to_id


def save_summary(conv_id: str, summary_text: str, through_id: int) -> None:
    db = get_db()
    db.execute(
        "UPDATE conversations SET summary = ?, summary_through_id = ? WHERE id = ?",
        (encrypt_text(summary_text), through_id, conv_id),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Retention / purge / account deletion
# ---------------------------------------------------------------------------

def get_retention_days(user_id: str) -> int | None:
    db = get_db()
    row = db.execute("SELECT retention_days FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["retention_days"] if row else None


def set_retention_days(user_id: str, days: int | None) -> None:
    db = get_db()
    db.execute("UPDATE users SET retention_days = ? WHERE id = ?", (days, user_id))
    db.commit()


def purge_expired_for_user(user_id: str) -> int:
    """Deletes conversations older than the user's retention setting.
    Returns the number deleted. No-op if retention is unset (keep forever)."""
    retention_days = get_retention_days(user_id)
    if not retention_days:
        return 0

    db = get_db()
    cursor = db.execute(
        "DELETE FROM conversations WHERE user_id = ? AND updated_at < datetime('now', ?)",
        (user_id, f"-{int(retention_days)} days"),
    )
    db.commit()
    return cursor.rowcount


def delete_account(user_id: str) -> None:
    """Deletes the user row — conversations and messages cascade via the
    foreign key ON DELETE CASCADE. This is permanent and irreversible."""
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
