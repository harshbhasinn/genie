"""The provider boundary.

Everything above this file is provider-agnostic. Swapping Gemini for anything
else means writing one more class implementing this protocol and changing
GENIE_LLM_PROVIDER — nothing else in the service knows which model is running.

Why a Protocol and not a base class: adapters have nothing to inherit. They
share a shape, not behaviour.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Message:
    role: str  # "user" | "model"
    text: str


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0       # the visible answer
    thinking_tokens: int = 0     # hidden reasoning - billed as output, and counts toward the cap

    @property
    def billed_output(self) -> int:
        return self.output_tokens + self.thinking_tokens

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.billed_output


@dataclass(slots=True)
class Completion:
    """Accumulated result of a stream. Usage arrives at the end, so it mutates."""
    text: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None


class LLMError(RuntimeError):
    """Provider call failed. Carries enough to tell the user something true."""

    def __init__(self, message: str, *, status: int | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@runtime_checkable
class Provider(Protocol):
    name: str

    async def stream(self, messages: Sequence[Message], *,
                     system: str | None = None,
                     temperature: float = 0.4,
                     max_output_tokens: int = 2048) -> AsyncIterator[str]:
        """Yield text chunks as they arrive."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch. Returns one vector per input, in order."""
        ...

    async def close(self) -> None:
        ...
