"""
Conversation store for Sibbu.

Thread-safe, in-memory, multi-conversation chat storage keyed by Flask
session id. Each browser session can hold several named conversations
(a ChatGPT-style sidebar), each with its own message history, language
lock, and expiry — instead of the single flat history the original
implementation kept.

Design notes / why this exists:
- The original `_chat_store` in app.py was a bare module-level dict
  mutated directly from request handlers with no lock. Under gunicorn's
  default sync worker that's one request at a time so it "worked", but
  the moment you add threads (`--worker-class gthread`, needed anyway
  for SSE streaming) or a second worker process touching the same
  session concurrently, that becomes a real race: two requests can
  read-modify-write `history` interleaved and drop a turn. Every mutation
  here goes through a single `threading.Lock`.
- It only ever supported one conversation per browser session. This
  store supports many, addressable by id, which is what makes a real
  chat history sidebar possible.
- It is still in-process memory on purpose: no external database, no
  paid add-on, nothing to configure to get the project running for
  free. That's a deliberate trade-off, not an oversight — see the
  README "Scaling beyond one process" section for the Redis swap-in
  path once you outgrow a single instance.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

MAX_CONVERSATIONS_PER_SESSION = 20
MAX_HISTORY_MESSAGES = 20
TITLE_MAX_LEN = 48
DEFAULT_TITLE = "New conversation"


@dataclass
class Conversation:
    id: str
    title: str = DEFAULT_TITLE
    history: list = field(default_factory=list)
    lang: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_summary(self) -> dict:
        return {"id": self.id, "title": self.title, "updated_at": self.updated_at.isoformat()}


class ConversationStore:
    def __init__(self, session_lifetime_hours: int):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._lifetime = timedelta(hours=session_lifetime_hours)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _prune_locked(self) -> None:
        now = self._now()
        expired = [sid for sid, s in self._sessions.items() if s["expires_at"] < now]
        for sid in expired:
            self._sessions.pop(sid, None)

    def _touch_locked(self, sid: str) -> None:
        self._sessions[sid]["expires_at"] = self._now() + self._lifetime

    def _get_or_create_session_locked(self, sid: str) -> dict:
        s = self._sessions.get(sid)
        if s is None:
            s = {"conversations": {}, "order": [], "expires_at": self._now() + self._lifetime}
            self._sessions[sid] = s
        return s

    def new_conversation(self, sid: str) -> Conversation:
        with self._lock:
            self._prune_locked()
            s = self._get_or_create_session_locked(sid)
            if len(s["order"]) >= MAX_CONVERSATIONS_PER_SESSION:
                oldest_id = s["order"].pop(0)
                s["conversations"].pop(oldest_id, None)
            conv = Conversation(id=str(uuid.uuid4()))
            s["conversations"][conv.id] = conv
            s["order"].append(conv.id)
            self._touch_locked(sid)
            return conv

    def list_conversations(self, sid: str) -> list[dict]:
        with self._lock:
            self._prune_locked()
            s = self._sessions.get(sid)
            if not s:
                return []
            convs = [s["conversations"][cid] for cid in s["order"] if cid in s["conversations"]]
            convs.sort(key=lambda c: c.updated_at, reverse=True)
            return [c.to_summary() for c in convs]

    def get(self, sid: str, conv_id: str) -> Conversation | None:
        with self._lock:
            self._prune_locked()
            s = self._sessions.get(sid)
            if not s:
                return None
            return s["conversations"].get(conv_id)

    def get_history(self, sid: str, conv_id: str) -> list[dict]:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return []
            conv = s["conversations"].get(conv_id)
            return list(conv.history) if conv else []

    def delete(self, sid: str, conv_id: str) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return
            s["conversations"].pop(conv_id, None)
            if conv_id in s["order"]:
                s["order"].remove(conv_id)

    def clear_session(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def set_lang_if_unset(self, sid: str, conv_id: str, lang: str) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return
            conv = s["conversations"].get(conv_id)
            if conv and conv.lang is None:
                conv.lang = lang

    def record_turn(self, sid: str, conv_id: str, role: str, content: str) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if not s:
                return
            conv = s["conversations"].get(conv_id)
            if not conv:
                return
            conv.history.append({"role": role, "content": content})
            conv.history = conv.history[-MAX_HISTORY_MESSAGES:]
            conv.updated_at = self._now()
            if conv.title == DEFAULT_TITLE and role == "user":
                conv.title = (content[:TITLE_MAX_LEN] + "…") if len(content) > TITLE_MAX_LEN else content
            self._touch_locked(sid)

    def session_size(self) -> int:
        """Number of active sessions currently held in memory (for /health diagnostics)."""
        with self._lock:
            self._prune_locked()
            return len(self._sessions)
