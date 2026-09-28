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

from genie.chat.responder import Responder
from genie.chat.session import MemorySessionStore
from genie.main import app
from genie.ontology import load

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client():
    """Force the stub by passing provider=None, whatever .env says."""
    ontology = load(REPO)
    with TestClient(app) as c:
        app.state.ontology = ontology
        app.state.provider = None
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
    assert body["ontology"]["capabilities"] == 49
    assert body["config"]["bff_mode"] in ("token", "admin")
    assert "gemini_api_key" not in json.dumps(body).lower()  # no secret leaks


def test_stream_is_well_formed(client):
    r = client.post("/v1/genie/chat", json={
        "message": "what is my wallet balance",
        "ui_context": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"},
    })
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    evs = events(r)
    assert evs[0][0] == "start"
    assert evs[-1][0] == "done"
    assert evs[0][1]["mode"] == "stub"
    assert evs[-1][1]["blocks"] > 0
    assert evs[-1][1]["latency_ms"] >= 0


def test_exact_utterance_surfaces_as_a_capability_block(client):
    r = client.post("/v1/genie/chat", json={
        "message": "what is my wallet balance",
        "ui_context": {"screen": "command_centre", "role": "AD_PARTNER_ADMIN"},
    })
    caps = [d for n, d in events(r) if n == "block" and d["type"] == "capability"]
    assert len(caps) == 1
    assert caps[0]["content"] == "wallet.explain_balance"
    assert caps[0]["meta"]["match"] == "exact-utterance"
    # On the Command Centre the question is genuinely ambiguous, so it must ask.
    assert "which_wallet" in caps[0]["meta"]["follow_ups"]


def test_same_words_on_the_answering_screen_do_not_ask(client):
    """g001 vs g002 from the golden set, as a transport test."""
    r = client.post("/v1/genie/chat", json={
        "message": "what is my wallet balance",
        "ui_context": {"screen": "wallet_management", "role": "AD_PARTNER_ADMIN"},
    })
    caps = [d for n, d in events(r) if n == "block" and d["type"] == "capability"]
    assert "which_wallet" not in caps[0]["meta"]["follow_ups"]


def test_session_is_continuous(client):
    first = client.post("/v1/genie/chat", json={"message": "hello"})
    sid = events(first)[0][1]["session_id"]
    second = client.post("/v1/genie/chat", json={"message": "again",
                                                 "session_id": sid})
    assert events(second)[0][1]["session_id"] == sid


def test_missing_context_still_answers(client):
    r = client.post("/v1/genie/chat", json={"message": "hello"})
    text = " ".join(d["content"] for n, d in events(r)
                    if n == "block" and d["type"] == "token")
    assert "No screen context" in text


def test_empty_message_is_rejected(client):
    assert client.post("/v1/genie/chat", json={"message": ""}).status_code == 422


def test_stub_says_it_is_a_stub(client):
    """An assistant that quietly degrades is worse than one that says so."""
    r = client.post("/v1/genie/chat", json={"message": "hello"})
    finals = [d for n, d in events(r) if n == "block" and d["type"] == "text"]
    assert finals and finals[0]["meta"]["stub"] is True
