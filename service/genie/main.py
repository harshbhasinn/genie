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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth import tokens as token_auth
from .bff import BffClient
from .chat.responder import Responder
from .chat.router import router as chat_router
from .chat.session import MemorySessionStore
from .config import settings
from .llm import build as build_provider
from .ontology import OntologyError, load

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
    app.state.sessions = MemorySessionStore()
    app.state.responder = Responder(ontology, provider, bff=bff)

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
        log.info("stopped")


app = FastAPI(
    title="Genie",
    description="Read-only assistant for the AdGrid Ad Partner Agency Admin Dashboard.",
    version="0.2.0",
    lifespan=lifespan,
)

# Local dev only. Production pins this to the dashboard origin.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(chat_router)


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
        "sessions": app.state.sessions.count(),
        "token": token_state,
    }


@app.get("/readyz", tags=["ops"])
async def readyz() -> dict:
    """Ready means the ontology loaded. The LLM and the BFF are not readiness
    conditions — the service answers definitions without either."""
    return {"ready": True, "metrics": app.state.ontology.stats["metrics"]}
