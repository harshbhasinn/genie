"""Rung 2: semantic match over the 244 authored utterances.

Held in memory as one numpy array, not in pgvector. 244 vectors is a 3 MB array
and a matrix multiply; a network hop per chat turn would cost more than the
search. pgvector earns its place at S10, when the corpus is thousands of chunks
needing metadata pre-filters.

Vectors are cached to disk keyed by a digest of the catalog, so a restart costs
nothing and a catalog edit re-embeds automatically. Same pattern as
render_kb.py's source_digest — the thing that changes is the thing that keys it.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..llm.base import LLMError, Provider

log = logging.getLogger(__name__)

CACHE_VERSION = 1


@dataclass(frozen=True, slots=True)
class VectorHit:
    capability_id: str
    utterance: str
    score: float


def digest(utterances: dict[str, str], model: str) -> str:
    payload = json.dumps({"v": CACHE_VERSION, "model": model,
                          "u": sorted(utterances.items())}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class CapabilityIndex:
    """Cosine similarity over L2-normalised utterance vectors."""

    def __init__(self, utterances: dict[str, str], model: str):
        self._texts = sorted(utterances)
        self._cap_ids = [utterances[t] for t in self._texts]
        self._model = model
        self._digest = digest(utterances, model)
        self._matrix: np.ndarray | None = None

    @property
    def ready(self) -> bool:
        return self._matrix is not None

    @property
    def size(self) -> int:
        return len(self._texts)

    @property
    def dim(self) -> int | None:
        return None if self._matrix is None else int(self._matrix.shape[1])

    def _cache_path(self, cache_dir: Path) -> Path:
        return cache_dir / f"capability-index-{self._digest}.npz"

    async def build(self, provider: Provider, cache_dir: Path,
                    expected_dim: int | None = None) -> None:
        """Load from cache, or embed and cache. Never fatal.

        A failure here degrades routing to exact + lexical, which still answers
        most of the golden set. Refusing to start over an embedding call would
        make a third party's uptime a boot dependency.
        """
        path = self._cache_path(cache_dir)
        if path.exists():
            try:
                self._matrix = np.load(path)["m"]
                log.info("capability index from cache: %d x %d",
                         *self._matrix.shape)
                return
            except (OSError, KeyError, ValueError) as err:
                log.warning("cache unreadable (%s), re-embedding", err)

        try:
            raw = await provider.embed(self._texts)
        except LLMError as err:
            log.warning("could not embed capability utterances: %s", err)
            return

        matrix = np.asarray(raw, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(self._texts):
            log.warning("embed returned an unexpected shape %s", matrix.shape)
            return
        if expected_dim and matrix.shape[1] != expected_dim:
            # Writing 768-wide vectors into a 3072-wide index produces plausible
            # nonsense rather than an error. Refuse instead.
            log.error("embedding width %d != expected %d — index disabled",
                      matrix.shape[1], expected_dim)
            return

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        self._matrix = matrix / np.maximum(norms, 1e-12)
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            np.savez_compressed(path, m=self._matrix)
        except OSError as err:
            log.warning("could not write index cache: %s", err)
        log.info("capability index built: %d utterances x %d dims",
                 *self._matrix.shape)

    async def search(self, provider: Provider, query: str, *,
                     limit: int = 40, floor: float = 0.55) -> list[VectorHit]:
        if self._matrix is None:
            return []
        try:
            vectors = await provider.embed([query])
        except LLMError as err:
            log.warning("query embed failed: %s", err)
            return []
        if not vectors:
            return []
        q = np.asarray(vectors[0], dtype=np.float32)
        if q.shape[0] != self._matrix.shape[1]:
            return []
        q /= max(float(np.linalg.norm(q)), 1e-12)

        scores = self._matrix @ q
        top = np.argsort(-scores)[:limit]
        return [VectorHit(self._cap_ids[i], self._texts[i], round(float(scores[i]), 4))
                for i in top if scores[i] >= floor]
