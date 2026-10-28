"""No conversation ever reaches another user.

Two users of the SAME partner - the hardest case, since everything else about them is
alike - each hold a conversation. Every way one could reach the other's is tried:
sending the other's session id, forging a token that claims the other, inventing an id,
tampering with a stored session. Plus: nothing is saved for an unverified caller, the
token itself is never stored, and Redis failing costs context, never an answer.

The same checks run against a real Redis in test_sessions_redis.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from genie.auth.tokens import NoToken
from genie.chat.responder import Responder
from genie.chat.session import MemorySessionStore, Session, SessionStore, UIContext
from genie.llm.base import Message
from genie.main import app
from genie.ontology import load
from genie.store import MemoryKV, RedisKV
from .test_wallet import CLIENT, FakeBff, client_for, envelope, jwt

REPO = Path(__file__).resolve().parents[2]
EXP = int(time.time()) + 300
TOKEN_A = jwt(sub="u-a", azp="adg-dashboard", exp=EXP)
TOKEN_B = jwt(sub="u-b", azp="adg-dashboard", exp=EXP)
FORGED = jwt(sub="u-a", azp="adg-dashboard", exp=EXP, forged=True)   # CLAIMS to be A
WHO = {TOKEN_A: "u-a", TOKEN_B: "u-b", FORGED: "u-b"}                  # what the BFF says


class PerUserBff(FakeBff):
    """`me` answers for whoever the token really belongs to - the BFF's verdict, not
    the token's claims. Everything else as the wallet tests' fake."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/me":
            self.calls.append(request)
            user = WHO[request.headers["authorization"].removeprefix("Bearer ")]
            return httpx.Response(200, json=envelope({
                "userId": user, "clientId": CLIENT, "primaryRole": "AD_PARTNER_ADMIN",
                "grants": [{"clientId": CLIENT, "clientName": "Infinity",
                            "role": "AD_PARTNER_ADMIN", "permissions": ["VIEW_WALLET"]}]}))
        return super().__call__(request)


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


@contextmanager
def serve(ontology, store: SessionStore, fake: FakeBff | None = None):
    fake = fake or PerUserBff()
    with TestClient(app) as c:
        app.state.ontology, app.state.provider = ontology, None
        app.state.tokens, app.state.bff = NoToken(), client_for(fake)
        app.state.sessions = store
        app.state.responder = Responder(ontology, None, bff=app.state.bff)
        yield c, fake


def turn(c, token, message, session_id=None):
    """(session id, how the turn was routed, the answer)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = c.post("/v1/genie/chat", headers=headers, json={
        "message": message, "sessionId": session_id,
        "uiContext": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"}})
    assert r.status_code == 200
    events, name = [], None
    for line in r.text.splitlines():
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: ") and name:
            events.append((name, json.loads(line[6:])))
    assert events[-1][0] == "done"
    blocks = [d for n, d in events if n == "block"]
    routed = next((b["meta"].get("match") for b in blocks if b["type"] == "capability"), None)
    return events[0][1]["sessionId"], routed, blocks[-1]["content"] if blocks else ""


# ── across users ─────────────────────────────────────────────────────────

def test_another_users_session_id_finds_nothing(ontology):
    with serve(ontology, MemorySessionStore()) as (c, _):
        sid_a, _, asked = turn(c, TOKEN_A, "what is my wallet balance")
        assert asked == "Which balance do you mean?"
        # B sends A's id with an answer to A's question: a fresh session, not A's.
        sid_b, routed_b, _ = turn(c, TOKEN_B, "Main wallet", sid_a)
        assert sid_b != sid_a and routed_b != "follow_up"
        # A's conversation is untouched: A's answer is still taken as the answer.
        again, routed_a, answer = turn(c, TOKEN_A, "Main wallet", sid_a)
        assert again == sid_a and routed_a == "follow_up" and "₹15,75,000" in answer


def test_a_forged_token_cannot_claim_a_session(ontology):
    """The token CLAIMS to be A; the BFF says it is B. B is who it gets."""
    with serve(ontology, MemorySessionStore()) as (c, _):
        sid_a, _, _ = turn(c, TOKEN_A, "what is my wallet balance")
        sid_f, routed, _ = turn(c, FORGED, "Main wallet", sid_a)
        assert sid_f != sid_a and routed != "follow_up"
        assert turn(c, TOKEN_A, "Main wallet", sid_a)[1] == "follow_up"


def test_two_users_answering_at_once_keep_their_own_answers(ontology):
    with serve(ontology, MemorySessionStore()) as (c, _):
        sid_a, _, _ = turn(c, TOKEN_A, "what is my wallet balance")
        sid_b, _, _ = turn(c, TOKEN_B, "what is my wallet balance")
        assert sid_a != sid_b
        assert turn(c, TOKEN_B, "Main wallet", sid_b)[:2] == (sid_b, "follow_up")
        assert turn(c, TOKEN_A, "Main wallet", sid_a)[:2] == (sid_a, "follow_up")


# ── the store's own rules ────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


def test_an_invented_or_malformed_id_is_never_adopted():
    store = MemorySessionStore()
    for proposed in ("f" * 32, "user:u-b:" + "f" * 32, "../" + "a" * 29, "A" * 32, ""):
        session = run(store.load("user:u-a", proposed))
        assert session.id != proposed and len(session.id) == 32 and session.owner == "user:u-a"


def test_a_stored_session_that_does_not_match_its_key_is_refused():
    kv = MemoryKV()
    store = SessionStore(kv)
    stolen = Session(id="a" * 32, owner="user:u-b", history=[Message("user", "B's secret")])
    run(kv.set(store._key("user:u-a", "a" * 32), json.dumps(stolen.to_dict()), 60))
    loaded = run(store.load("user:u-a", "a" * 32))
    assert loaded.id != "a" * 32 and loaded.history == []


def test_nothing_is_saved_for_an_unverified_caller(ontology):
    """The BFF is configured but nobody is verified (no token): a fresh session every
    turn, and nothing written."""
    store = MemorySessionStore()
    with serve(ontology, store) as (c, _):
        first, _, _ = turn(c, None, "what is my wallet balance")
        second, routed, _ = turn(c, None, "Main wallet", first)
    assert first != second and routed != "follow_up"
    assert not [k for k in store.kv._items if ":session:" in k]


def test_a_deployed_genie_without_a_bff_never_pools_its_callers(ontology):
    """Admin mode or a misconfiguration leaves no BFF outside a local run. Only a LOCAL run
    may share one owner; anywhere else two callers must never share a session."""
    store = MemorySessionStore()
    with serve(ontology, store) as (c, _):
        app.state.bff, app.state.responder = None, Responder(ontology, None)
        app.state.settings = app.state.settings.model_copy(update={"genie_env": "qa"})
        first, _, _ = turn(c, None, "what is my wallet balance")
        second, routed, _ = turn(c, None, "Main wallet", first)
    assert first != second and routed != "follow_up"
    assert not [k for k in store.kv._items if ":session:" in k]


def test_the_me_cache_reads_once_per_token_and_never_stores_the_token(ontology):
    store = MemorySessionStore()
    with serve(ontology, store) as (c, fake):
        sid, _, _ = turn(c, TOKEN_A, "what is my wallet balance")
        turn(c, TOKEN_A, "Main wallet", sid)
        turn(c, TOKEN_B, "what is my wallet balance")
    me_calls = [r.headers["authorization"] for r in fake.calls if r.url.path == "/api/v1/me"]
    assert me_calls == [f"Bearer {TOKEN_A}", f"Bearer {TOKEN_B}"]        # once per token
    stored = json.dumps(list(store.kv._items.items()))
    assert TOKEN_A not in stored and TOKEN_B not in stored
    assert f"genie:me:{hashlib.sha256(TOKEN_A.encode()).hexdigest()}" in store.kv._items
    assert f"genie:session:user:u-a:{sid}" in store.kv._items


def test_a_session_keeps_every_field_through_storage():
    s = Session(id="b" * 32, owner="user:u-a", history=[Message("user", "hi"), Message("model", "₹1")],
                context=UIContext(screen="wallet_management", role="CAMPAIGN_MANAGER", client_id=CLIENT,
                                  visible_metrics=("main_wallet_balance",), selected_entity="x", brand_id="b-1"),
                asked={"which_wallet": 1}, permissions=frozenset({"VIEW_WALLET"}),
                pending={"capability": "wallet.explain_balance", "text": "balance",
                         "options": [["main", "Main wallet"]]},
                entities={"Diwali Blast": "campaign"}, names_read_for=CLIENT)
    back = Session.from_dict(json.loads(json.dumps(s.to_dict())))
    for f in Session.__slots__:
        if f != "identity":
            assert getattr(back, f) == getattr(s, f), f


def test_memory_store_expires_and_is_bounded():
    now = [0.0]
    kv = MemoryKV(max_items=2, clock=lambda: now[0])
    run(kv.set("a", "1", 10))
    now[0] = 11
    assert run(kv.get("a")) is None                                   # expired
    for k in "bcd":
        run(kv.set(k, k, 10))
    assert run(kv.get("b")) is None and run(kv.get("d")) == "d"       # oldest went first


# ── Redis down: context lost, answers kept ───────────────────────────────

def test_redis_down_costs_context_not_answers(ontology):
    dead = SessionStore(RedisKV("redis://127.0.0.1:1/0", timeout_s=0.2))
    with serve(ontology, dead) as (c, _):
        first, _, asked = turn(c, TOKEN_A, "what is my wallet balance")
        second, routed, answer = turn(c, TOKEN_A, "Main wallet", first)
        health = c.get("/healthz").json()["sessions"]
    assert asked == "Which balance do you mean?" and answer                        # still answered
    assert second != first and routed != "follow_up"                   # only context lost
    assert health["kind"] == "redis" and health["reachable"] is False and health["errors"] > 0
