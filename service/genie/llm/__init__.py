"""Provider selection. The only place that knows which adapter exists."""
from __future__ import annotations

from ..config import Settings
from .base import Completion, LLMError, Message, Provider, Usage
from .gemini import GeminiProvider

__all__ = ["Completion", "LLMError", "Message", "Provider", "Usage", "build"]


def build(settings: Settings) -> Provider | None:
    """Returns None when the LLM is not configured.

    Deliberately not an error: S2's exit gate is a streamed response through a
    traced, authenticated pipe. That is testable with the stub responder and no
    key at all, and a missing key should not stop the service booting.
    """
    if not settings.llm_configured:
        return None
    provider = settings.genie_llm_provider
    if provider == "gemini":
        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.genie_llm_model,
            embed_model=settings.genie_embed_model,
            thinking_budget=settings.genie_llm_thinking_budget,
            thinking_level=settings.genie_llm_thinking_level or None,
        )
    raise LLMError(f"unknown GENIE_LLM_PROVIDER {provider!r} — "
                   f"add an adapter in genie/llm/ implementing Provider")
