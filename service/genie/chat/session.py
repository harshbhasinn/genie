"""Conversation state, and the rule that keeps one user's conversation from ever
reaching another.

A session is stored under its OWNER and its id: `genie:session:{owner}:{session_id}`.
The owner is the user id the `me` endpoint returned for the request's token - the
BFF answers `me` only for a token it has verified - never a claim read from the token
and never anything the browser sends. So another user's session id simply finds
nothing under your owner: there is no comparison to get wrong.

Three more rules close the remaining gaps:
  - an unknown id is never adopted - a fresh session gets a new random id;
  - a loaded session must carry the owner it was looked up by, or it is refused;
  - no verified owner means nothing is saved: a fresh session every turn.

Where the sessions live is the KeyValue's business (genie/store.py): this process's
memory by default, Redis when GENIE_REDIS_URL is set, so every Genie task shares them.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

from ..llm.base import Message
from ..store import KeyValue, MemoryKV

log = logging.getLogger(__name__)

MAX_TURNS = 20
SESSION_TTL_S = 60 * 60
KEY_PREFIX = "genie:"
SESSION_ID = re.compile(r"[0-9a-f]{32}")                  # what uuid4().hex produces


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
    brand_id: str | None = None          # the brand whose pages the user is in, if any

    @classmethod
    def parse(cls, raw: dict | None) -> "UIContext":
        raw = raw or {}
        metrics = raw.get("visible_metrics") or []
        return cls(
            screen=raw.get("screen"), role=raw.get("role"),
            client_id=raw.get("client_id"),
            visible_metrics=tuple(str(m) for m in metrics),
            selected_entity=raw.get("selected_entity"),
            brand_id=raw.get("brand_id"),
        )

    def to_dict(self) -> dict:
        return {"screen": self.screen, "role": self.role, "client_id": self.client_id,
                "visible_metrics": list(self.visible_metrics),
                "selected_entity": self.selected_entity, "brand_id": self.brand_id}


@dataclass(slots=True)
class Session:
    id: str
    # Whose session this is: the verified user id, LOCAL_OWNER without a BFF, or None
    # when nobody could be verified (then it is never saved).
    owner: str | None = None
    created_at: float = field(default_factory=time.time)
    touched_at: float = field(default_factory=time.time)
    history: list[Message] = field(default_factory=list)
    context: UIContext = field(default_factory=UIContext)
    # follow-up id -> times asked. max_asks is honoured ACROSS turns, not within
    # one, or a user gets the same question every time they rephrase.
    asked: dict[str, int] = field(default_factory=dict)
    # The grant's permissions from the `me` endpoint (chat/router.py), when their vocabulary
    # matches the catalog's; empty otherwise, and then role alone gates.
    permissions: frozenset[str] = frozenset()
    # Who the token says this is (genie.auth.identity.Identity), set afresh on every
    # request and never stored with the session.
    identity: object | None = None
    # The follow-up asked last turn, so "Main wallet" is taken as the answer to
    # "Which balance?" rather than routed from scratch.
    pending: dict | None = None
    # Names of the user's own things that live answers have shown (a campaign, a
    # brand), name -> kind, so a name is routed as a name and not as its words.
    entities: dict[str, str] = field(default_factory=dict)
    # The account whose campaign and brand names have been read into `entities` -
    # once per session, before its first question is routed.
    names_read_for: str | None = None

    def remember(self, kind: str, names) -> None:
        for name in names:
            if name and len(name.strip()) >= 3:
                self.entities[name.strip()] = kind

    def add(self, role: str, text: str) -> None:
        self.history.append(Message(role=role, text=text))
        # Keep the tail. Trimming from the front loses the oldest turn, which is
        # the right thing to lose.
        if len(self.history) > MAX_TURNS * 2:
            del self.history[: len(self.history) - MAX_TURNS * 2]
        self.touched_at = time.time()

    def to_dict(self) -> dict:
        """Everything but the identity, which is read afresh on every request."""
        return {"id": self.id, "owner": self.owner,
                "created_at": self.created_at, "touched_at": self.touched_at,
                "history": [[m.role, m.text] for m in self.history],
                "context": self.context.to_dict(), "asked": self.asked,
                "permissions": sorted(self.permissions), "pending": self.pending,
                "entities": self.entities, "names_read_for": self.names_read_for}

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(id=d["id"], owner=d["owner"],
                   created_at=d["created_at"], touched_at=d["touched_at"],
                   history=[Message(role=r, text=t) for r, t in d.get("history") or []],
                   context=UIContext.parse(d.get("context")), asked=dict(d.get("asked") or {}),
                   permissions=frozenset(d.get("permissions") or ()), pending=d.get("pending"),
                   entities=dict(d.get("entities") or {}), names_read_for=d.get("names_read_for"))


class SessionStore:
    """Sessions in a KeyValue, keyed by owner and id."""

    def __init__(self, kv: KeyValue, ttl_s: int = SESSION_TTL_S, prefix: str = KEY_PREFIX):
        self.kv = kv
        self._ttl = ttl_s
        self.prefix = prefix

    def _key(self, owner: str, session_id: str) -> str:
        return f"{self.prefix}session:{owner}:{session_id}"

    async def load(self, owner: str | None, session_id: str | None) -> Session:
        """This owner's session with this id - or a fresh one with a NEW random id. An id
        must look like one Genie issued, so nothing the browser sends shapes the key."""
        if owner and session_id and SESSION_ID.fullmatch(session_id):
            raw = await self.kv.get(self._key(owner, session_id))
            if raw is not None:
                try:
                    session = Session.from_dict(json.loads(raw))
                except (ValueError, KeyError, TypeError):
                    log.warning("an unreadable session was dropped")
                else:
                    if session.owner == owner and session.id == session_id:
                        return session
                    log.error("a stored session did not match the key it was stored under")
        return Session(id=uuid.uuid4().hex, owner=owner)

    async def save(self, session: Session) -> None:
        """Nothing is saved for a session without an owner."""
        if not session.owner:
            return
        session.touched_at = time.time()
        await self.kv.set(self._key(session.owner, session.id),
                          json.dumps(session.to_dict(), ensure_ascii=False), self._ttl)


class MemorySessionStore(SessionStore):
    """Sessions in this process's memory - one Genie process only."""

    def __init__(self, ttl_s: int = SESSION_TTL_S):
        super().__init__(MemoryKV(), ttl_s=ttl_s)
