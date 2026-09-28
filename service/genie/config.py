"""Settings, from .env. Secrets are SecretStr so they cannot be logged by accident.

Everything a deployment changes lives here. Nothing else reads os.environ.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


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

    # ── AdGrid BFF ─────────────────────────────────────────────────────
    # token = forward a user JWT. admin = unauthenticated admin endpoints.
    genie_bff_mode: str = "token"
    genie_bff_base: str = "https://ag-dashboard-bff.qa.adgrid.ai"
    genie_bff_admin_base: str = ""
    genie_bff_token_file: Path = REPO_ROOT / "secrets" / "bff.jwt"

    # ── pgvector (not used until end of S2) ────────────────────────────
    genie_pg_dsn: SecretStr = SecretStr("")

    @field_validator("genie_llm_provider", "genie_bff_mode")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("genie_bff_mode")
    @classmethod
    def _known_mode(cls, v: str) -> str:
        if v not in ("token", "admin"):
            raise ValueError("GENIE_BFF_MODE must be 'token' or 'admin'")
        return v

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
