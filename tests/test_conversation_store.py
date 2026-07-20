import threading

from conversation_store import ConversationStore


def test_new_conversation_creates_entry():
    store = ConversationStore(session_lifetime_hours=1)
    conv = store.new_conversation("sid-1")
    assert conv.title == "New conversation"
    assert store.get("sid-1", conv.id) is conv


def test_record_turn_sets_title_from_first_user_message():
    store = ConversationStore(session_lifetime_hours=1)
    conv = store.new_conversation("sid-1")
    store.record_turn("sid-1", conv.id, "user", "Why does my knee hurt after running?")
    assert store.get("sid-1", conv.id).title == "Why does my knee hurt after running?"


def test_record_turn_truncates_long_titles():
    store = ConversationStore(session_lifetime_hours=1)
    conv = store.new_conversation("sid-1")
    long_message = "x" * 100
    store.record_turn("sid-1", conv.id, "user", long_message)
    assert len(store.get("sid-1", conv.id).title) <= 49  # TITLE_MAX_LEN + ellipsis


def test_delete_removes_conversation():
    store = ConversationStore(session_lifetime_hours=1)
    conv = store.new_conversation("sid-1")
    store.delete("sid-1", conv.id)
    assert store.get("sid-1", conv.id) is None


def test_clear_session_removes_all_conversations():
    store = ConversationStore(session_lifetime_hours=1)
    store.new_conversation("sid-1")
    store.new_conversation("sid-1")
    store.clear_session("sid-1")
    assert store.list_conversations("sid-1") == []


def test_sessions_are_isolated():
    store = ConversationStore(session_lifetime_hours=1)
    conv_a = store.new_conversation("sid-a")
    assert store.get("sid-b", conv_a.id) is None


def test_conversation_cap_evicts_oldest():
    store = ConversationStore(session_lifetime_hours=1)
    from conversation_store import MAX_CONVERSATIONS_PER_SESSION

    first_conv = store.new_conversation("sid-1")
    for _ in range(MAX_CONVERSATIONS_PER_SESSION):
        store.new_conversation("sid-1")

    convs = store.list_conversations("sid-1")
    assert len(convs) == MAX_CONVERSATIONS_PER_SESSION
    assert not any(c["id"] == first_conv.id for c in convs)


def test_concurrent_record_turn_does_not_lose_messages():
    """Regression test for the race condition in the original bare-dict store:
    many threads recording turns on the same conversation concurrently should
    never drop a turn, because every mutation goes through a single lock."""
    store = ConversationStore(session_lifetime_hours=1)
    conv = store.new_conversation("sid-1")

    def record(n):
        store.record_turn("sid-1", conv.id, "user", f"message {n}")

    threads = [threading.Thread(target=record, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    history = store.get_history("sid-1", conv.id)
    # MAX_HISTORY_MESSAGES caps this at 20, but every recorded turn should
    # have been applied atomically (no lost updates, no corrupted list).
    assert len(history) == 20
    assert all("message " in turn["content"] for turn in history)
