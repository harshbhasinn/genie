"""Settings, from .env. Secrets are SecretStr so they cannot be logged by accident.

Everything a deployment changes lives here. Nothing else reads os.environ.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
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
    genie_env: str = "local"
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

    # ── pgvector (not used until end of S2) ────────────────────────────
    genie_pg_dsn: SecretStr = SecretStr("")

    @field_validator("genie_llm_provider", "genie_bff_mode", "genie_env")
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
        }


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
