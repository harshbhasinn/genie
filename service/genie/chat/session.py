"""Conversation state.

In-process for S2, behind a Protocol so Postgres at S4 is a swap rather than a
rewrite. Bounded on purpose: an unbounded dict in a long-running service is a
memory leak wearing a cache costume.
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol

from ..llm.base import Message

MAX_SESSIONS = 500
MAX_TURNS = 20
SESSION_TTL_S = 60 * 60


@dataclass(slots=True)
class UIContext:
    """What the user is looking at. Not decoration — it suppresses follow-ups.

    'what is my wallet balance' on the Command Centre must ask which balance.
    The same words on Wallet Management must not. That difference is this object.
    """
    screen: str | None = None
    role: str | None = None
    client_id: str | None = None
    visible_metrics: tuple[str, ...] = ()
    selected_entity: str | None = None

    @classmethod
    def parse(cls, raw: dict | None) -> "UIContext":
        raw = raw or {}
        metrics = raw.get("visible_metrics") or []
        return cls(
            screen=raw.get("screen"), role=raw.get("role"),
            client_id=raw.get("client_id"),
            visible_metrics=tuple(str(m) for m in metrics),
            selected_entity=raw.get("selected_entity"),
        )


@dataclass(slots=True)
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    history: list[Message] = field(default_factory=list)
    context: UIContext = field(default_factory=UIContext)

    def add(self, role: str, text: str) -> None:
        self.history.append(Message(role=role, text=text))
        # Keep the tail. Trimming from the front loses the oldest turn, which is
        # the right thing to lose.
        if len(self.history) > MAX_TURNS * 2:
            del self.history[: len(self.history) - MAX_TURNS * 2]
        self.touched_at = time.time()


class SessionStore(Protocol):
    def get_or_create(self, session_id: str | None) -> Session:
        ...

    def count(self) -> int:
        ...


class MemorySessionStore(SessionStore):
    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl_s: int = SESSION_TTL_S):
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._max = max_sessions
        self._ttl = ttl_s

    def _evict(self) -> None:
        cutoff = time.time() - self._ttl
        for sid in [s for s, v in self._sessions.items() if v.touched_at < cutoff]:
            del self._sessions[sid]
        while len(self._sessions) > self._max:
            self._sessions.popitem(last=False)

    def get_or_create(self, session_id: str | None) -> Session:
        self._evict()
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            self._sessions.move_to_end(session_id)
            return session
        sid = session_id or uuid.uuid4().hex
        session = Session(id=sid)
        self._sessions[sid] = session
        return session

    def count(self) -> int:
        return len(self._sessions)
