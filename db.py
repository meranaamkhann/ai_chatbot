"""
SQLite data layer for Sibbu.

Why SQLite and not Postgres/a managed DB: this project's whole premise is
$0 to run. SQLite is a single file, ships in the Python standard library,
needs no separate service, and comfortably handles the traffic a free-tier
single-instance deployment will ever see.

Migrations: SQLite can add nullable columns to an existing table
(`ALTER TABLE ... ADD COLUMN`) but can't change a column's constraints
in place. Rather than requiring you to delete your live database every
time the schema grows, `_run_migrations()` checks what columns already
exist (via `PRAGMA table_info`) and adds only what's missing, every time
the app starts. This is intentionally simple — no migration framework,
no versioned migration files — because at this scale a framework buys
you very little and is one more thing to explain in an interview. It does
mean migrations here can only ever *add* nullable columns, never rename
or tighten one; a real schema framework (Alembic) is the natural next
step if that stops being enough.

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
    password_hash TEXT,
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

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# (table, column, sqlite type/default) — added via ALTER TABLE if missing.
# Kept separate from SCHEMA because SQLite can't express "add this column
# only if it doesn't already exist" inside CREATE TABLE for existing tables.
_COLUMN_MIGRATIONS = [
    ("users", "oauth_provider", "TEXT"),
    ("users", "oauth_id", "TEXT"),
    ("users", "retention_days", "INTEGER"),
    ("conversations", "summary", "TEXT"),
    ("conversations", "summary_through_id", "INTEGER"),
]


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _run_migrations(conn: sqlite3.Connection) -> None:
    for table, column, coltype in _COLUMN_MIGRATIONS:
        if column not in _existing_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

    # Partial unique index: two NULL oauth_ids don't collide (every
    # password-only account has oauth_provider/oauth_id = NULL), but two
    # accounts can't claim the same (provider, id) pair.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_oauth "
        "ON users(oauth_provider, oauth_id) WHERE oauth_provider IS NOT NULL"
    )
    conn.commit()


def init_db() -> None:
    """Create tables if they don't exist yet and run column migrations.
    Safe to call on every app startup, including against an existing
    database created by an earlier version of this schema."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        _run_migrations(conn)
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
