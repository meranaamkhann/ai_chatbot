"""
SQLite data layer for Sibbu.

Why SQLite and not Postgres/a managed DB: this project's whole premise is
$0 to run. SQLite is a single file, ships in the Python standard library,
needs no separate service, and comfortably handles the traffic a free-tier
single-instance deployment will ever see. The schema below is intentionally
plain — three tables, foreign keys, no ORM — because an ORM buys you very
little at this scale and is one more thing to explain in an interview.

Connection handling: one connection per request, opened lazily and stored
on Flask's `g`, closed in a `teardown_appcontext` hook. sqlite3 connections
are not safe to share across threads, so "one per request" is the standard
safe pattern for a threaded Flask app (see Procfile's `--worker-class
gthread`) rather than one long-lived global connection.
"""

from __future__ import annotations

import os
import sqlite3

from flask import g

DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sibbu.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New conversation',
    lang TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
"""


def init_db() -> None:
    """Create tables if they don't exist yet. Safe to call on every app startup."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    """Return this request's SQLite connection, opening one on first use."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exception=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
