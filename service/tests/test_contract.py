"""What the frontend can rely on, for chat and the calculator alike:

  - every refusal has one shape {code, message, retryable} - including a malformed request,
    which also names the fields at fault and never echoes the body back
  - nothing internal reaches the browser: no exception names, no provider messages
  - a stream always ends with `done`, after an `error` if something failed part-way
  - CORS: the dashboard origin is named when deployed (never "*"), and Retry-After is readable
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from genie.config import Settings
from genie.main import app
from .test_planner_api import TOKENS, ScopedSource, body, install, one, result


def test_a_malformed_request_has_the_common_error_shape_and_echoes_nothing():
    with TestClient(app) as c:
        install(ScopedSource())
        for path, payload, field in (
                ("/v1/genie/plan", {**body(), "budget": None}, "budget"),
                ("/v1/genie/plan", {**body(), "imageSlots": "three"}, "imageSlots"),
                ("/v1/genie/chat", {"message": ""}, "message")):
            r = c.post(path, json={k: v for k, v in payload.items() if v is not None})
            assert r.status_code == 422
            got = r.json()
            assert set(got) == {"code", "message", "retryable", "fields"}
            assert got["code"] == "INVALID_REQUEST" and got["retryable"] is False
            assert field in [f["field"] for f in got["fields"]]
            assert "input" not in r.text and "35000" not in r.text


def test_the_calculator_takes_one_spelling_per_field():
    install(ScopedSource())
    snake = {k: v for k, v in body().items() if k not in ("imageSlots", "videoSlots")}
    r = one(TOKENS[0], {**snake, "image_slots": 3, "video_slots": 2})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST"


def test_money_is_always_to_the_paisa():
    install(ScopedSource())
    r = one(TOKENS[0], body(budget=35000.5, target=None))
    answer = result(r)
    plan = answer["plans"][0]
    assert answer["budget"] == "35000.50"
    assert plan["budgetLeft"] == "825.50" and plan["cost"] == "34175.00"
    assert all(ln["listCost"].count(".") == 1 and len(ln["listCost"].split(".")[1]) == 2
               for ln in plan["lines"])


def test_a_plan_that_fails_is_a_coded_500_without_internals(monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr("genie.planner.router.plan_json", broken)
    install(ScopedSource())
    r = one(TOKENS[0], body())
    assert r.status_code == 500
    assert r.json() == {"code": "INTERNAL_ERROR", "message": "Genie couldn't finish this plan. Try again.",
                        "retryable": True}
    assert "secret" not in r.text
    assert app.state.planner.in_flight == 0


def test_a_chat_turn_that_fails_says_so_without_internals(monkeypatch):
    with TestClient(app) as c:
        install(ScopedSource())

        async def boom(*a, **kw):
            raise KeyError("GENIE_INTERNAL_THING")
            yield  # pragma: no cover
        monkeypatch.setattr(app.state.responder, "respond", boom)
        r = c.post("/v1/genie/chat", json={"message": "hello"})
    assert r.status_code == 500
    assert r.json() == {"code": "INTERNAL_ERROR", "message": "Genie couldn't finish this answer. Try again.",
                        "retryable": True}
    assert "KeyError" not in r.text and "GENIE_INTERNAL_THING" not in r.text


def test_cors_names_the_dashboard_when_deployed_and_never_allows_every_site():
    assert Settings(_env_file=None, genie_env="local").cors_origins == ("*",)
    assert Settings(_env_file=None, genie_env="qa").cors_origins == ()
    named = Settings(_env_file=None, genie_env="prod",
                     genie_cors_origins="https://dashboard.adgrid.ai/, https://other.adgrid.ai")
    assert named.cors_origins == ("https://dashboard.adgrid.ai", "https://other.adgrid.ai")


def test_a_browser_can_read_retry_after():
    install(ScopedSource())
    with TestClient(app) as c:
        r = c.post("/v1/genie/plan", json=body(), headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-expose-headers") == "Retry-After"


def test_a_refusal_before_the_stream_is_json_with_a_code():
    install(None)
    r = one(TOKENS[0], body())
    assert r.headers["content-type"].startswith("application/json")
    assert set(r.json()) == {"code", "message", "retryable"}


# ── camelCase on the wire, in and out ─────────────────────────────────────────

def _keys(value, path=""):
    """Every key anywhere in a JSON value, with where it was."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, f"{path}.{k}"
            yield from _keys(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _keys(v, f"{path}[{i}]")


def _snake(response) -> list[str]:
    return [where for k, where in _keys(response.json()) if "_" in k]


def test_chat_refuses_the_old_snake_case_spellings():
    with TestClient(app) as c:
        install(ScopedSource())
        for bad in ({"message": "hi", "session_id": "0" * 32},
                    {"message": "hi", "ui_context": {"screen": "command_centre"}},
                    {"message": "hi", "uiContext": {"client_id": "x"}}):
            r = c.post("/v1/genie/chat", json=bad)
            assert r.status_code == 422 and r.json()["code"] == "INVALID_REQUEST", bad


def test_every_golden_question_streams_camel_case_keys_only():
    """All 121 golden questions, through the template responder (no model, no network),
    signed in against the fake BFF so live cards are included too."""
    import yaml

    from genie.chat.responder import Responder
    from .test_sessions import REPO, TOKEN_A, PerUserBff
    from .test_wallet import client_for

    golden = yaml.safe_load((REPO / "tests" / "golden-queries.yaml").read_text(encoding="utf-8"))
    with TestClient(app) as c:
        install(ScopedSource())
        app.state.bff = client_for(PerUserBff())
        app.state.provider = None
        app.state.responder = Responder(app.state.ontology, None, bff=app.state.bff)
        found = []
        for q in golden["queries"]:
            ctx = q.get("context") or {}
            r = c.post("/v1/genie/chat", headers={"Authorization": f"Bearer {TOKEN_A}"},
                       json={"message": q["query"],
                             "uiContext": {k: v for k, v in ctx.items() if k in ("screen", "role")}})
            assert r.status_code == 200, q["id"]
            found += [f"{q['id']}: {w}" for w in _snake(r)]
    assert not found, found[:10]


def test_every_calculator_event_is_camel_case():
    install(ScopedSource())
    for payload in (body(alternatives=2), body(target=None, alternatives=2),
                    body(budget="100", target=None), body(target=10 ** 7)):
        r = one(TOKENS[0], payload)
        assert r.status_code == 200 and not _snake(r), _snake(r)
