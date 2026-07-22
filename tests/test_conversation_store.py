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


# ---------------------------------------------------------------------------
# Rolling summarization
# ---------------------------------------------------------------------------

def test_needs_summarization_false_below_trigger(ctx):
    conv = ctx.new_conversation("user-1")
    for i in range(ctx.SUMMARY_TRIGGER_COUNT - 1):
        ctx.record_turn(conv["id"], "user" if i % 2 == 0 else "assistant", f"message {i}")
    assert ctx.needs_summarization(conv["id"]) is False


def test_needs_summarization_true_at_trigger(ctx):
    conv = ctx.new_conversation("user-1")
    for i in range(ctx.SUMMARY_TRIGGER_COUNT):
        ctx.record_turn(conv["id"], "user" if i % 2 == 0 else "assistant", f"message {i}")
    assert ctx.needs_summarization(conv["id"]) is True


def test_messages_to_fold_excludes_recent_window(ctx):
    conv = ctx.new_conversation("user-1")
    for i in range(ctx.SUMMARY_TRIGGER_COUNT):
        ctx.record_turn(conv["id"], "user" if i % 2 == 0 else "assistant", f"message {i}")

    to_fold, through_id = ctx.messages_to_fold_into_summary(conv["id"])
    assert through_id is not None
    assert len(to_fold) == ctx.SUMMARY_TRIGGER_COUNT - ctx.RECENT_WINDOW_FOR_PROMPT
    # The most recent messages should NOT be in the fold list.
    folded_contents = {m["content"] for m in to_fold}
    assert f"message {ctx.SUMMARY_TRIGGER_COUNT - 1}" not in folded_contents


def test_save_and_read_summary(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "first message")
    ctx.save_summary(conv["id"], "User discussed a headache.", through_id=1)

    summary, recent = ctx.get_prompt_context(conv["id"])
    assert summary == "User discussed a headache."


def test_get_prompt_context_only_includes_messages_after_summary(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "old message")  # id 1
    ctx.record_turn(conv["id"], "assistant", "old reply")  # id 2
    ctx.save_summary(conv["id"], "Summary of old exchange.", through_id=2)
    ctx.record_turn(conv["id"], "user", "new message")  # id 3

    summary, recent = ctx.get_prompt_context(conv["id"])
    assert summary == "Summary of old exchange."
    assert [m["content"] for m in recent] == ["new message"]


# ---------------------------------------------------------------------------
# Retention / purge
# ---------------------------------------------------------------------------

def test_retention_default_is_none_keep_forever(ctx):
    assert ctx.get_retention_days("user-1") is None


def test_set_and_get_retention_days(ctx):
    ctx.set_retention_days("user-1", 30)
    assert ctx.get_retention_days("user-1") == 30


def test_purge_expired_is_noop_when_retention_unset(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "hello")
    deleted = ctx.purge_expired_for_user("user-1")
    assert deleted == 0
    assert ctx.owns_conversation("user-1", conv["id"])


def test_purge_expired_deletes_old_conversations(ctx):
    import db as db_module

    conv = ctx.new_conversation("user-1")
    ctx.set_retention_days("user-1", 7)

    # Backdate updated_at to 30 days ago, well past the 7-day retention.
    conn = db_module.get_db()
    conn.execute(
        "UPDATE conversations SET updated_at = datetime('now', '-30 days') WHERE id = ?", (conv["id"],)
    )
    conn.commit()

    deleted = ctx.purge_expired_for_user("user-1")
    assert deleted == 1
    assert not ctx.owns_conversation("user-1", conv["id"])


def test_purge_expired_keeps_recent_conversations(ctx):
    conv = ctx.new_conversation("user-1")
    ctx.set_retention_days("user-1", 30)
    deleted = ctx.purge_expired_for_user("user-1")
    assert deleted == 0
    assert ctx.owns_conversation("user-1", conv["id"])


# ---------------------------------------------------------------------------
# Account deletion
# ---------------------------------------------------------------------------

def test_delete_account_cascades_to_conversations_and_messages(ctx):
    import db as db_module

    conv = ctx.new_conversation("user-1")
    ctx.record_turn(conv["id"], "user", "hello")

    ctx.delete_account("user-1")

    conn = db_module.get_db()
    user_row = conn.execute("SELECT 1 FROM users WHERE id = ?", ("user-1",)).fetchone()
    conv_row = conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conv["id"],)).fetchone()
    msg_rows = conn.execute(
        "SELECT 1 FROM messages WHERE conversation_id = ?", (conv["id"],)
    ).fetchall()

    assert user_row is None
    assert conv_row is None
    assert msg_rows == []
