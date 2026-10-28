"""The Genie service.

    uvicorn genie.main:app --reload --port 9030     (from service/)

Boots in this order, and refuses to start if the first step fails:
  1. ontology + catalog, validated, including the read-only invariant
  2. LLM provider, if configured — a missing key is not fatal
  3. token source
"""
from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import tokens as token_auth
from .bff import BffClient
from .chat.responder import Responder
from .chat.router import router as chat_router
from .chat.session import SessionStore
from .config import settings
from .llm import build as build_provider
from .ontology import OntologyError, load
from .planner.cache import PlanningInputs
from .planner.engine import Planner
from .planner.router import router as plan_router
from .planner.source import build as build_plan_source
from .store import build as build_store

log = logging.getLogger("genie")
STARTED = time.time()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = settings()
    _configure_logging(cfg.genie_log_level)
    log.info("starting genie | %s", cfg.redacted())

    try:
        ontology = load(cfg.genie_ontology_root)
    except OntologyError as err:
        # A service that boots with a half-loaded ontology answers questions
        # about metrics it does not understand. Die with the reason instead.
        log.error("ONTOLOGY FAILED TO LOAD — refusing to start\n  %s", err)
        sys.exit(1)

    provider = build_provider(cfg)
    if provider is None:
        log.warning("no LLM configured — stub responder. Set GEMINI_API_KEY and "
                    "GENIE_LLM_MODEL (see tools/list_models.py)")

    # The read path. Config has already refused any host that is not QA.
    bff = (BffClient(cfg.bff_base, portal_id=cfg.genie_bff_portal_id,
                     timeout=cfg.genie_bff_timeout_s)
           if cfg.genie_bff_mode == "token" else None)
    if bff is not None and not cfg.genie_bff_portal_id:
        log.warning("GENIE_BFF_PORTAL_ID is empty — every BFF call will be refused "
                    "with PORTAL_ID_REQUIRED")

    app.state.settings = cfg
    app.state.ontology = ontology
    app.state.provider = provider
    app.state.tokens = token_auth.build(cfg)
    app.state.bff = bff
    # Sessions (and the `me` cache) in Redis when GENIE_REDIS_URL is set, so every Genie
    # task shares them; in this process's memory otherwise.
    app.state.sessions = SessionStore(build_store(cfg.genie_redis_url.get_secret_value()),
                                      ttl_s=cfg.genie_session_ttl_s)
    app.state.responder = Responder(ontology, provider, bff=bff,
                                    live_wording=cfg.genie_live_wording)
    # The calculator: solver processes (started at boot, scipy loaded once each)
    # and its inputs, cached in the same store as sessions. Plans themselves are never stored.
    app.state.planner = Planner(cfg.genie_plan_workers,
                                max_in_flight=cfg.genie_plan_max_in_flight,
                                per_user_per_min=cfg.genie_plan_per_user_per_min)
    plan_source = build_plan_source(cfg.genie_plan_source)
    if plan_source is not None:
        app.state.planner.warm()
    app.state.plan_inputs = (PlanningInputs(app.state.sessions.kv, plan_source,
                                            prefix=app.state.sessions.prefix)
                             if plan_source is not None else None)

    log.info("ready on http://%s:%s | responder=%s | bff=%s",
             cfg.genie_host, cfg.genie_port, app.state.responder.mode,
             bff.base if bff else "off")
    try:
        yield
    finally:
        if provider is not None:
            await provider.close()
        if bff is not None:
            await bff.close()
        app.state.planner.close()
        await app.state.sessions.kv.close()
        log.info("stopped")


app = FastAPI(
    title="Genie",
    description="Read-only assistant for the AdGrid partner dashboard.",
    version="0.2.0",
    lifespan=lifespan,
)

# Which browser origins may call Genie: GENIE_CORS_ORIGINS. Read at import, because
# middleware is fixed before the app starts. Retry-After is exposed so a browser can read
# how long to wait after a 429 or 503.
_cors = settings().cors_origins
if _cors:
    app.add_middleware(
        CORSMiddleware, allow_origins=list(_cors), allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"], expose_headers=["Retry-After"],
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Every refusal has one shape: {code, message, retryable}, plus the fields at fault.
    The request body is never echoed back."""
    fields = [{"field": ".".join(str(p) for p in e.get("loc", ()) if p != "body"),
               "problem": e.get("msg", "invalid")} for e in exc.errors()]
    return JSONResponse({"code": "INVALID_REQUEST",
                         "message": "Some fields are missing or invalid.",
                         "retryable": False, "fields": fields}, status_code=422)

app.include_router(chat_router)
app.include_router(plan_router)


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict:
    """Everything needed to answer 'why is it behaving like that' in one call."""
    cfg = app.state.settings
    ontology = app.state.ontology
    info = app.state.tokens.info()

    token_state: dict[str, object] = {"source": app.state.tokens.name}
    if info:
        token_state.update({
            "roles": list(info.roles), "user_id": info.user_id,
            "expires_in_s": info.expires_in, "expired": info.expired,
        })
        # The single most likely reason a BFF read fails. Say it here, loudly,
        # rather than making someone read a 401 body.
        if info.expired:
            token_state["hint"] = "token expired — replace secrets/bff.jwt"
        elif (info.expires_in or 0) < 60:
            token_state["hint"] = "token expires in under a minute"
    elif cfg.genie_bff_mode == "token" and cfg.genie_env != "local":
        token_state["hint"] = "tokens are forwarded by the dashboard on each request"
    elif cfg.genie_bff_mode == "token":
        token_state["hint"] = f"no token at {cfg.genie_bff_token_file}"
    tokens = app.state.tokens
    if getattr(tokens, "can_refresh", False):
        token_state["can_refresh"] = True
    if getattr(tokens, "last_refresh_error", None):
        token_state["refresh_error"] = tokens.last_refresh_error
    token_state["portal_id_set"] = bool(cfg.genie_bff_portal_id)

    return {
        "status": "ok",
        "uptime_s": int(time.time() - STARTED),
        "config": cfg.redacted(),
        "ontology": ontology.stats,
        "responder": app.state.responder.mode,
        "sessions": await app.state.sessions.kv.health(),
        "planner": {"source": cfg.genie_plan_source, "connected": app.state.plan_inputs is not None,
                    "workers": app.state.planner.workers, "in_flight": app.state.planner.in_flight},
        "token": token_state,
    }


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict:
    """Ready means the ontology loaded. The LLM and the BFF are not readiness
    conditions — the service answers definitions without either."""
    return {"ready": True, "metrics": app.state.ontology.stats["metrics"]}
