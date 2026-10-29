"""Settings, from .env. Secrets are SecretStr so they cannot be logged by accident.

Everything a deployment changes lives here. Nothing else reads os.environ.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# The only BFF hosts this build may send a user's token to. Development and QA
# verification run against QA; production is a deliberate change to this line,
# made in review, not an .env edit made at 2 a.m.
ALLOWED_BFF_HOST_SUFFIXES = (".qa.adgrid.ai",)


def is_allowed_bff_host(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host.endswith(s) for s in ALLOWED_BFF_HOST_SUFFIXES)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── service ────────────────────────────────────────────────────────
    genie_host: str = "127.0.0.1"
    genie_port: int = 9030
    genie_log_level: str = "info"
    # REQUIRED - no default. "local" allows things a deployment must never do (dev tokens
    # from secrets/, any origin, demo data), so forgetting it must stop the service rather
    # than quietly make it local.
    genie_env: str
    genie_ontology_root: Path = REPO_ROOT

    # ── LLM ────────────────────────────────────────────────────────────
    genie_llm_provider: str = "gemini"
    gemini_api_key: SecretStr = SecretStr("")
    genie_llm_model: str = ""
    genie_embed_model: str = ""
    # "auto" = measure at first embed call and assert against the column width.
    genie_embed_dim: str = "auto"
    # Hidden reasoning tokens per call; 0 = off. Unset (the default) sends nothing,
    # so the model's own default applies - some models reject the setting outright.
    genie_llm_thinking_budget: int | None = None
    # For models that take a thinking LEVEL (Gemini 3.5: minimal, low, medium, high;
    # minimal is the floor - it cannot be turned off). Wins over the budget when set.
    genie_llm_thinking_level: str = ""

    # Who writes the sentence under a live card. "template": code (cannot invent a
    # number or a screen; free; instant). "model": the LLM rephrases the same facts,
    # and anything with a number not on the card falls back to the template.
    genie_live_wording: str = "template"

    @field_validator("genie_llm_thinking_budget", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if v in ("", None) else v

    # ── AdGrid BFF ─────────────────────────────────────────────────────
    # token = forward a user JWT. admin = unauthenticated admin endpoints.
    genie_bff_mode: str = "token"
    genie_bff_base: str = "https://ag-dashboard-bff.qa.adgrid.ai"
    genie_bff_admin_base: str = ""
    genie_bff_token_file: Path = REPO_ROOT / "secrets" / "bff.jwt"
    # Dev only: lets the service renew the 5-minute QA access token itself.
    genie_bff_refresh_file: Path = REPO_ROOT / "secrets" / "bff-refresh.token"
    genie_bff_timeout_s: float = 6.0
    # Every BFF call carries x-portal-id (a global security scheme in the spec,
    # e.g. ADOPS_PORTAL). The agency dashboard's own value - copy it from the
    # dashboard's requests. Without it the BFF answers PORTAL_ID_REQUIRED.
    genie_bff_portal_id: str = ""

    # ── sessions and short-lived caches ───────────────────────────────
    # Unset: this process's memory (one Genie process). Set: Redis, shared by every Genie
    # task - redis://:password@host:port/db, or rediss:// for TLS.
    genie_redis_url: SecretStr = SecretStr("")
    genie_session_ttl_s: int = 3600

    # ── browser access ─────────────────────────────────────────────────
    # Origins allowed to call Genie from a browser, comma-separated, e.g.
    # https://dashboard.qa.adgrid.ai. Unset: "*" on a developer's machine, and NONE when
    # deployed - a deployment names the dashboard's origin, it never allows every site.
    genie_cors_origins: str = ""

    # ── campaign calculator ───────────────────────────────────────────
    # Free screens, brand prices and quotes: off (the endpoint answers 503), bff (the
    # dashboard's own availability-search, client-rate-cards and simulate-cost, read as the
    # user) or fixture (demo data, LOCAL ONLY).
    genie_plan_source: str = "off"
    # Reach per screen: off or fixture (demo, LOCAL ONLY). No API serves it yet - it is the
    # one input the calculator still needs from another team.
    genie_plan_reach: str = "off"
    # Solver processes. One per vCPU the task has: os.cpu_count() reports the HOST's
    # cores inside a container, not the task's share, so it is set, never guessed.
    genie_plan_workers: int = 1
    # Plans running or waiting in this task before new ones are told to retry (503).
    genie_plan_max_in_flight: int = 4
    genie_plan_per_user_per_min: int = 10
    # Each solve (plan 1; then the alternatives) must be PROVEN within this, or the user
    # is told to try again - an unproven plan is never shown.
    genie_plan_time_limit_s: float = 3.0
    genie_plan_max_window_days: int = 90
    # Alternatives are shown only within this % of plan 1's cost (or reach).
    genie_plan_alternatives_within_pct: int = 10

    # ── pgvector (not used until end of S2) ────────────────────────────
    genie_pg_dsn: SecretStr = SecretStr("")

    @field_validator("genie_llm_provider", "genie_bff_mode", "genie_env", "genie_live_wording")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("genie_env")
    @classmethod
    def _known_env(cls, v: str) -> str:
        # "local" is the only environment that may read a token from secrets/.
        if v not in ("local", "qa", "prod"):
            raise ValueError("GENIE_ENV must be 'local', 'qa' or 'prod'")
        return v

    @field_validator("genie_plan_source")
    @classmethod
    def _known_plan_source(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("off", "bff", "fixture"):
            raise ValueError("GENIE_PLAN_SOURCE must be 'off', 'bff' or 'fixture'")
        return v

    @field_validator("genie_plan_reach")
    @classmethod
    def _known_plan_reach(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("off", "fixture"):
            raise ValueError("GENIE_PLAN_REACH must be 'off' or 'fixture'")
        return v

    @field_validator("genie_plan_workers", "genie_plan_max_in_flight", "genie_plan_per_user_per_min",
                     "genie_plan_max_window_days")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be at least 1")
        return v

    @field_validator("genie_plan_time_limit_s")
    @classmethod
    def _time_limit(cls, v: float) -> float:
        if not 0.5 <= v <= 30:
            raise ValueError("GENIE_PLAN_TIME_LIMIT_S must be between 0.5 and 30 seconds")
        return v

    @field_validator("genie_plan_alternatives_within_pct")
    @classmethod
    def _within(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("GENIE_PLAN_ALTERNATIVES_WITHIN_PCT must be 0 to 100")
        return v

    @model_validator(mode="after")
    def _fixture_is_local_only(self):
        # Demo prices or demo reach must never be shown to a real user.
        if self.genie_env != "local":
            for name, value in (("GENIE_PLAN_SOURCE", self.genie_plan_source),
                                ("GENIE_PLAN_REACH", self.genie_plan_reach)):
                if value == "fixture":
                    raise ValueError(f"{name}=fixture is demo data and only runs with GENIE_ENV=local")
        return self

    @field_validator("genie_live_wording")
    @classmethod
    def _known_wording(cls, v: str) -> str:
        if v not in ("template", "model"):
            raise ValueError("GENIE_LIVE_WORDING must be 'template' or 'model'")
        return v

    @field_validator("genie_bff_mode")
    @classmethod
    def _known_mode(cls, v: str) -> str:
        if v not in ("token", "admin"):
            raise ValueError("GENIE_BFF_MODE must be 'token' or 'admin'")
        return v

    @field_validator("genie_bff_token_file", "genie_bff_refresh_file")
    @classmethod
    def _from_repo_root(cls, v: Path) -> Path:
        # .env says ./secrets/bff.jwt; that must mean the repo's secrets/, not
        # whichever directory the service happened to be started from.
        return v if v.is_absolute() else (REPO_ROOT / v).resolve()

    @field_validator("genie_bff_base", "genie_bff_admin_base")
    @classmethod
    def _qa_only(cls, v: str) -> str:
        # A user's token goes wherever this points. Refuse to start rather than
        # discover at the first request that it pointed at production.
        if v and not is_allowed_bff_host(v):
            raise ValueError(
                f"BFF host {urlsplit(v).hostname!r} is not allowed. This build talks to "
                f"QA only (https://*{ALLOWED_BFF_HOST_SUFFIXES[0]}).")
        return v.rstrip("/")

    # ── derived ────────────────────────────────────────────────────────
    @property
    def llm_configured(self) -> bool:
        return bool(self.gemini_api_key.get_secret_value() and self.genie_llm_model)

    @property
    def cors_origins(self) -> tuple[str, ...]:
        named = tuple(o.strip().rstrip("/") for o in self.genie_cors_origins.split(",") if o.strip())
        if named:
            return named
        return ("*",) if self.genie_env == "local" else ()

    @property
    def bff_base(self) -> str:
        """Admin mode uses its own base if one is set, else the same host."""
        if self.genie_bff_mode == "admin" and self.genie_bff_admin_base:
            return self.genie_bff_admin_base.rstrip("/")
        return self.genie_bff_base.rstrip("/")

    def redacted(self) -> dict[str, object]:
        """Safe to log and to return from /healthz. No secret ever appears."""
        return {
            "env": self.genie_env,
            "llm_provider": self.genie_llm_provider,
            "llm_model": self.genie_llm_model or None,
            "embed_model": self.genie_embed_model or None,
            "embed_dim": self.genie_embed_dim,
            "llm_configured": self.llm_configured,
            "bff_mode": self.genie_bff_mode,
            "bff_base": self.bff_base,
            "pg_configured": bool(self.genie_pg_dsn.get_secret_value()),
            "redis_configured": bool(self.genie_redis_url.get_secret_value()),
            "cors_origins": list(self.cors_origins),
            "plan_source": self.genie_plan_source,
            "plan_reach": self.genie_plan_reach,
            "plan_workers": self.genie_plan_workers,
        }


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
