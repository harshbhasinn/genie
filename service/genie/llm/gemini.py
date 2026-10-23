"""Gemini adapter — Google AI Studio (Generative Language API).

Auth is a static API key in a header. If AdGrid ever needs inference to stay in
India, that is Vertex AI with a service account and the region in the URL path —
a second adapter beside this one, not a change to it.
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence

import httpx

from .base import LLMError, Message, Provider, Usage

log = logging.getLogger(__name__)

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, embed_model: str,
                 timeout: float = 60.0, thinking_budget: int | None = None):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is empty")
        if not model:
            raise LLMError("GENIE_LLM_MODEL is empty — run tools/list_models.py")
        self._model = model
        # Genie's model jobs are choosing from a list and wording given facts, so
        # hidden reasoning buys nothing and costs seconds: measured on
        # gemini-3.8-flash, ~3.9 s with default thinking vs ~2.6 s with budget 0.
        # None sends no thinking setting at all (for models that reject one).
        self._thinking_budget = thinking_budget
        self._embed_model = embed_model
        self._last_usage = Usage()
        self._client = httpx.AsyncClient(
            base_url=BASE, timeout=timeout,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        )

    @property
    def last_usage(self) -> Usage:
        return self._last_usage

    # ── generation ─────────────────────────────────────────────────────
    async def stream(self, messages: Sequence[Message], *,
                     system: str | None = None, temperature: float = 0.4,
                     max_output_tokens: int = 2048) -> AsyncIterator[str]:
        body: dict = {
            "contents": [{"role": m.role, "parts": [{"text": m.text}]} for m in messages],
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_output_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if self._thinking_budget is not None:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}

        url = f"/models/{self._model}:streamGenerateContent?alt=sse"
        self._last_usage = Usage()
        try:
            async with self._client.stream("POST", url, json=body) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode(errors="replace")
                    raise LLMError(_explain(resp.status_code, raw),
                                   status=resp.status_code,
                                   retryable=resp.status_code in (429, 500, 503))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    for text in _texts(chunk):
                        yield text
                    if meta := chunk.get("usageMetadata"):
                        self._last_usage = Usage(
                            prompt_tokens=meta.get("promptTokenCount", 0),
                            output_tokens=meta.get("candidatesTokenCount", 0))
        except httpx.RequestError as err:
            raise LLMError(f"cannot reach Gemini: {err}", retryable=True) from err

    # ── embeddings ─────────────────────────────────────────────────────
    # Hard server limit. Exceeding it returns 400, not a truncated result, so it
    # fails loudly — but only if the caller chunks. 244 utterances is 3 calls.
    MAX_BATCH = 100

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._embed_model:
            raise LLMError("GENIE_EMBED_MODEL is empty — run tools/list_models.py")
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self.MAX_BATCH):
            out.extend(await self._embed_batch(texts[start:start + self.MAX_BATCH]))
        if len(out) != len(texts):
            raise LLMError(f"embed returned {len(out)} vectors for {len(texts)} inputs")
        return out

    async def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        body = {"requests": [
            {"model": f"models/{self._embed_model}",
             "content": {"parts": [{"text": t}]}} for t in texts]}
        try:
            resp = await self._client.post(
                f"/models/{self._embed_model}:batchEmbedContents", json=body)
        except httpx.RequestError as err:
            raise LLMError(f"cannot reach Gemini: {err}", retryable=True) from err
        if resp.status_code != 200:
            raise LLMError(_explain(resp.status_code, resp.text),
                           status=resp.status_code,
                           retryable=resp.status_code in (429, 500, 503))
        return [e.get("values") or [] for e in resp.json().get("embeddings", [])]

    async def close(self) -> None:
        await self._client.aclose()


def _texts(chunk: dict) -> list[str]:
    out = []
    for cand in chunk.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            if text := part.get("text"):
                out.append(text)
    return out


def _explain(status: int, raw: str) -> str:
    """Turn a provider error into something that says what to do about it."""
    try:
        detail = json.loads(raw).get("error", {}).get("message", "")
    except (json.JSONDecodeError, AttributeError):
        detail = raw[:200]
    hint = {
        400: " — malformed request; check batch size and model id",
        401: " — GEMINI_API_KEY is wrong",
        403: " — key restricted, or the Generative Language API is off for that project",
        404: " — that model id does not exist for this key; run tools/list_models.py",
        429: " — rate limited",
    }.get(status, "")
    return f"Gemini {status}: {detail}{hint}"
