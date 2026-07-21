import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def ctx(monkeypatch):
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setenv("DATABASE_PATH", str(Path(tmp_dir) / "test.db"))
    monkeypatch.setenv("ENCRYPTION_KEY", "tkGtljKdQPNaeYmt3KQr--k-_13LdA-qc0tUjoeDpLY=")

    for mod in ("db", "conversation_store", "crypto"):
        sys.modules.pop(mod, None)

    import db
    import conversation_store as store

    from flask import Flask

    app = Flask(__name__)
    db.init_db()

    with app.app_context():
        conn = db.get_db()
        for uid in ("user-1", "user-2"):
            conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                (uid, f"{uid}@example.com", "not-a-real-hash"),
            )
        conn.commit()
        yield store

    for mod in ("db", "conversation_store", "crypto"):
        sys.modules.pop(mod, None)


def test_new_conversation_creates_row(ctx):
    conv = ctx.new_conversation("user-1")
    assert conv["title"] == "New conversation"
    assert ctx.owns_conversation("user-1", conv["id"])


def test_record_turn_sets_title_from_first_user_message(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "Why does my knee hurt after running?")
    listed = ctx.list_conversations("user-1")
    assert listed[0]["title"] == "Why does my knee hurt after running?"


def test_record_turn_truncates_long_titles(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "x" * 100)
    listed = ctx.list_conversations("user-1")
    assert len(listed[0]["title"]) <= 49


def test_get_history_returns_messages_in_order(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "first")
    ctx.record_turn(conv["id"], "assistant", "second")
    history = ctx.get_history(conv["id"], limit=None)
    assert [m["content"] for m in history] == ["first", "second"]


def test_delete_conversation_requires_matching_owner(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.delete_conversation("user-2", conv["id"])  # wrong owner: no-op
    assert ctx.owns_conversation("user-1", conv["id"])

    ctx.delete_conversation("user-1", conv["id"])
    assert not ctx.owns_conversation("user-1", conv["id"])


def test_clear_user_conversations_only_affects_that_user(ctx):
    conv_a = ctx.new_conversation("user-1")
    conv_b = ctx.new_conversation("user-2")

    ctx.clear_user_conversations("user-1")

    assert not ctx.owns_conversation("user-1", conv_a["id"])
    assert ctx.owns_conversation("user-2", conv_b["id"])


def test_conversation_cap_evicts_oldest(ctx):
    first = ctx.new_conversation("user-1")
    for _ in range(ctx.MAX_CONVERSATIONS_PER_USER):
        ctx.new_conversation("user-1")

    listed = ctx.list_conversations("user-1")
    assert len(listed) == ctx.MAX_CONVERSATIONS_PER_USER
    assert not any(c["id"] == first["id"] for c in listed)


def test_persists_across_reconnect(ctx, monkeypatch):
    """Regression test for the original in-memory store's core weakness:
    data must survive the process/connection being torn down and reopened,
    not just live for the duration of one request."""
    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "does this survive a restart?")

    import db

    db.close_db()  # simulate the connection being torn down, as happens every request

    history = ctx.get_history(conv["id"], limit=None)
    assert len(history) == 1
    assert history[0]["content"] == "does this survive a restart?"
