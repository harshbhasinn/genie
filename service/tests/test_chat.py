"""The chat transport: SSE framing, session continuity, context handling.

Runs against the STUB responder throughout — no API key, no network, no tokens
spent. That is deliberate: the transport has to be testable without a third
party, or CI depends on someone else's uptime.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from genie.auth.tokens import NoToken
from genie.chat.responder import Responder
from genie.chat.session import MemorySessionStore
from genie.main import app
from genie.ontology import load

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client():
    """Force the stub by passing provider=None, whatever .env says — and no token or
    BFF, so a developer's real QA token in secrets/ is never used by a test."""
    ontology = load(REPO)
    with TestClient(app) as c:
        app.state.ontology = ontology
        app.state.provider = None
        app.state.tokens = NoToken()
        app.state.bff = None
        app.state.sessions = MemorySessionStore()
        app.state.responder = Responder(ontology, None)
        yield c


def events(response) -> list[tuple[str, dict]]:
    out, name = [], None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: ") and name:
            out.append((name, json.loads(line[6:])))
    return out


def test_health_reports_real_state(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["ontology"]["capabilities"] == 57
    assert body["config"]["bff_mode"] in ("token", "admin")
    assert "gemini_api_key" not in json.dumps(body).lower()  # no secret leaks


def test_stream_is_well_formed(client):
    r = client.post("/v1/genie/chat", json={
        "message": "what is my wallet balance",
        "uiContext": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"},
    })
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    evs = events(r)
    assert evs[0][0] == "start"
    assert evs[-1][0] == "done"
    assert evs[0][1]["mode"] == "stub"
    assert evs[-1][1]["blocks"] > 0
    assert evs[-1][1]["latencyMs"] >= 0


def test_exact_utterance_routes_and_asks(client):
    """g001: three balances exist and the Command Centre names none, so it asks."""
    r = client.post("/v1/genie/chat", json={
        "message": "what is my wallet balance",
        "uiContext": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"},
    })
    evs = events(r)
    caps = [d for n, d in evs if n == "block" and d["type"] == "capability"]
    assert len(caps) == 1
    assert caps[0]["content"] == "wallet.explain_balance"
    assert caps[0]["meta"]["match"] == "exact"
    assert caps[0]["meta"]["confidence"] == 1.0

    asks = [d for n, d in evs if n == "block" and d["type"] == "follow_up"]
    assert len(asks) == 1
    assert asks[0]["meta"]["id"] == "which_wallet"
    # Options carry hints, or the user cannot tell the three apart.
    assert all(o["hint"] for o in asks[0]["meta"]["options"])


def test_same_words_on_the_answering_screen_do_not_ask(client):
    """g002: identical words, different screen, and the question must NOT fire.

    The entire argument for embedding rather than a chat window is this pair.
    """
    r = client.post("/v1/genie/chat", json={
        "message": "what is my wallet balance",
        "uiContext": {"screen": "wallet_management", "role": "AD_PARTNER_ADMIN"},
    })
    evs = events(r)
    caps = [d for n, d in evs if n == "block" and d["type"] == "capability"]
    assert caps[0]["content"] == "wallet.explain_balance"
    assert not [d for n, d in evs if n == "block" and d["type"] == "follow_up"]


def test_navigation_never_calls_the_model(client):
    """NAVIGATE has a 200 ms p95 budget. It emits a route, not a sentence."""
    r = client.post("/v1/genie/chat", json={
        "message": "take me to the wallet",
        "uiContext": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"},
    })
    evs = events(r)
    nav = [d for n, d in evs if n == "block" and d["type"] == "navigate"]
    assert len(nav) == 1
    assert nav[0]["content"] == "/wallet-management"
    assert nav[0]["meta"]["screenId"] == "wallet_management"
    assert not [d for n, d in evs if n == "block" and d["type"] == "token"]


def test_otp_refusal_text_is_authored_not_generated(client):
    """An OTP refusal must read the same every time, so it is served verbatim."""
    r = client.post("/v1/genie/chat", json={
        "message": "top up my wallet",
        "uiContext": {"screen": "wallet_management", "role": "AD_PARTNER_ADMIN"},
    })
    evs = events(r)
    caps = [d for n, d in evs if n == "block" and d["type"] == "capability"]
    assert caps[0]["meta"]["otpGated"] is True
    refusals = [d for n, d in evs if n == "block" and d["type"] == "refusal"]
    assert len(refusals) == 1
    assert refusals[0]["meta"]["verbatim"] is True
    assert "OTP" in refusals[0]["content"]


def test_definition_is_served_verbatim(client):
    """Definitions come from the YAML, never paraphrased, never vector-retrieved."""
    r = client.post("/v1/genie/chat", json={
        "message": "what does verified impressions mean",
        "uiContext": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"},
    })
    evs = events(r)
    defs = [d for n, d in evs if n == "block" and d["type"] == "definition"]
    assert defs and defs[0]["meta"]["verbatim"] is True
    # Blocked metric: the definition is known, the FIGURE is not served.
    gaps = [d for n, d in evs if n == "block" and d["type"] == "unavailable"]
    assert gaps and "R7" in gaps[0]["meta"]["blockedBy"]


def test_session_is_continuous(client):
    first = client.post("/v1/genie/chat", json={"message": "hello"})
    sid = events(first)[0][1]["sessionId"]
    second = client.post("/v1/genie/chat", json={"message": "again",
                                                 "sessionId": sid})
    assert events(second)[0][1]["sessionId"] == sid


def test_unmatched_query_says_so_rather_than_guessing(client):
    """Routing to the nearest topical capability on no evidence is the failure
    mode that loses trust. Saying 'I do not know what you mean' is the answer."""
    evs = events(client.post("/v1/genie/chat", json={"message": "hello"}))
    caps = [d for n, d in evs if n == "block" and d["type"] == "capability"]
    assert caps[0]["content"] == ""
    assert caps[0]["meta"]["match"] == "none"
    finals = [d for n, d in evs if n == "block" and d["type"] == "text"]
    assert finals[0]["meta"]["unrouted"] is True


def test_empty_message_is_rejected(client):
    assert client.post("/v1/genie/chat", json={"message": ""}).status_code == 422


def test_stub_says_it_is_a_stub(client):
    """An assistant that quietly degrades is worse than one that says so."""
    r = client.post("/v1/genie/chat", json={
        "message": "what does verified impressions mean",
        "uiContext": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"}})
    finals = [d for n, d in events(r) if n == "block" and d["type"] == "text"]
    assert finals and finals[0]["meta"]["stub"] is True
