"""The calculator's INPUTS, cached. Plans are never cached or stored anywhere.

  free screens     {prefix}plan:free:{owner}:{client}:{sha256(query)}       90 s
  rates + reach    {prefix}plan:rr:{owner}:{client}:{ratesVersion}:{reachVersion}   6 h

Keyed by the verified OWNER (the user), not just the partner. The backend may one day
show two users of the same partner different contracts (a manager scoped to some
brands), and a key shared by both would hand one the other's view. Per-user keys make
that impossible whatever the backend decides; widen to partner x access level only once
the backend confirms its scoping rule.

Rates and reach are versioned: every free-screens response carries the current versions,
so a cached rate card is used only while its version is still the current one, and a
changed rate is read fresh at once.

What is stored is the backend's `data`, already checked, and it is checked AGAIN on the
way out - a corrupted or tampered cache value is refused, never planned from.

Stored COMPRESSED: free-screen data is mostly repeated small integers, so ~93 KB of JSON
today is ~5 KB stored (~2 MB -> ~100 KB at 100 cities x 20 categories). The Redis it
shares with chat sessions then never evicts a conversation to make room for a cache.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import zlib

from ..store import KeyValue
from .contract import (Availability, AvailabilityQuery, ContractError, RatesAndReach,
                       parse_availability, parse_rates_and_reach)
from .source import PlanningSource

log = logging.getLogger(__name__)

FREE_TTL_S = 90
RATES_TTL_S = 6 * 60 * 60
PACKED = "z1:"                      # zlib + base64; the KV holds text


def pack(data) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return PACKED + base64.b64encode(zlib.compress(raw, 6)).decode()


def unpack(text: str):
    if not text.startswith(PACKED):
        raise ValueError("not a packed value")
    return json.loads(zlib.decompress(base64.b64decode(text[len(PACKED):], validate=True)))


class PlanningInputs:
    def __init__(self, kv: KeyValue, source: PlanningSource, prefix: str = "genie:",
                 free_ttl_s: int = FREE_TTL_S, rates_ttl_s: int = RATES_TTL_S):
        self.kv, self.source, self.prefix = kv, source, prefix
        self.free_ttl_s, self.rates_ttl_s = free_ttl_s, rates_ttl_s

    async def availability(self, token: str | None, owner: str, client_id: str,
                           q: AvailabilityQuery) -> Availability:
        digest = hashlib.sha256(q.key().encode()).hexdigest()
        key = f"{self.prefix}plan:free:{owner}:{client_id}:{digest}"
        cached = await self._read(key, lambda d: parse_availability(d, q, client_id))
        if cached is not None:
            return cached
        data = await self.source.availability(token, client_id, q)
        parsed = parse_availability(data, q, client_id)              # raises ContractError
        await self.kv.set(key, pack(data), self.free_ttl_s)
        return parsed

    async def rates_and_reach(self, token: str | None, owner: str, client_id: str,
                              rates_version: str, reach_version: str) -> RatesAndReach:
        key = f"{self.prefix}plan:rr:{owner}:{client_id}:{rates_version}:{reach_version}"
        cached = await self._read(key, lambda d: parse_rates_and_reach(d, client_id))
        if cached is not None:
            return cached
        data = await self.source.rates_and_reach(token, client_id)
        parsed = parse_rates_and_reach(data, client_id)
        # Cached under the versions it CARRIES. If they moved between the two reads, this
        # is the newer data; the next free-screens read will name these versions.
        key = f"{self.prefix}plan:rr:{owner}:{client_id}:{parsed.rates_version}:{parsed.reach_version}"
        await self.kv.set(key, pack(data), self.rates_ttl_s)
        return parsed

    async def _read(self, key: str, parse):
        raw = await self.kv.get(key)
        if raw is None:
            return None
        try:
            return parse(unpack(raw))
        except (ValueError, binascii.Error, zlib.error, ContractError) as err:
            log.warning("planning cache value refused (%s) - reading fresh", type(err).__name__)
            await self.kv.delete(key)
            return None
