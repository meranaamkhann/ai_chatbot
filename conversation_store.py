"""
Conversation store for Sibbu — SQLite-backed, keyed by user account.

This replaces the earlier in-memory, session-keyed store. Two things
changed the requirements enough to warrant that:

1. Conversations now belong to a *user account*, not a browser session —
   the whole point of adding login is that your chats follow you across
   devices and survive a server restart. An in-memory dict can't do that.
2. A single dyno restart (which happens routinely on free hosting tiers —
   deploys, idle spin-down, etc.) used to silently wipe every
   conversation. That's a real data-loss bug for anything calling itself
   more than a demo.

Still zero added infrastructure cost: SQLite is a file on disk, not a
service you pay for or configure. See db.py for the schema and connection
handling, and AUDIT.md / README's "Scaling beyond one instance" section
for when this stops being enough (answer: swap SQLite for Postgres behind
the same function signatures below — nothing above this module needs to
change).
"""

from __future__ import annotations

import uuid

from db import get_db

MAX_CONVERSATIONS_PER_USER = 50
MAX_HISTORY_MESSAGES = 20
TITLE_MAX_LEN = 48
DEFAULT_TITLE = "New conversation"


def new_conversation(user_id: str) -> dict:
    db = get_db()
    conv_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
        (conv_id, user_id, DEFAULT_TITLE),
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
    return [dict(row) for row in rows]


def owns_conversation(user_id: str, conv_id: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
    ).fetchone()
    return row is not None


def get_history(conv_id: str, limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conv_id,),
    ).fetchall()
    trimmed = rows[-limit:] if limit else rows
    return [dict(row) for row in trimmed]


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
        (conv_id, role, content),
    )

    if role == "user":
        row = db.execute("SELECT title FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if row and row["title"] == DEFAULT_TITLE:
            title = (content[:TITLE_MAX_LEN] + "…") if len(content) > TITLE_MAX_LEN else content
            db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))

    db.execute("UPDATE conversations SET updated_at = datetime('now') WHERE id = ?", (conv_id,))
    db.commit()
