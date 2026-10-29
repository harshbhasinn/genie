"""The calculator's INPUTS, cached. Plans and quotes are never cached or stored anywhere.

  free screens   {prefix}plan:free:{owner}:{client}:{sha256(question)}    90 s
  rate cards     {prefix}plan:rates:{owner}:{client}                      5 min
  reach          {prefix}plan:reach:{owner}:{client}                      1 h

Keyed by the verified OWNER (the user), not just the partner. The backend may show two
users of the same partner different things (a manager scoped to some brands), and a key
shared by both would hand one the other's view. Per-user keys make that impossible whatever
the backend decides.

Rate cards carry no version on the BFF, so they are kept only 5 minutes - and every plan
is priced again by simulate-cost before it is shown, so a changed rate can never reach a
user as a final price.

What is stored is the backend's `data`, already checked, and it is checked AGAIN on the
way out - a corrupted or tampered value is refused, never planned from. Stored compressed
(zlib), so the Redis it shares with chat sessions never evicts a conversation for a cache.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import zlib
from decimal import Decimal

from ..store import KeyValue
from .contract import (Availability, ContractError, PlanQuery, RateCard, Reach,
                       parse_availability, parse_quote, parse_rate_cards, parse_reach)
from .source import PlanningSource, ReachSource

log = logging.getLogger(__name__)

FREE_TTL_S = 90
RATES_TTL_S = 5 * 60
REACH_TTL_S = 60 * 60
PACKED = "z1:"                      # zlib + base64; the KV holds text


def pack(data) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return PACKED + base64.b64encode(zlib.compress(raw, 6)).decode()


def unpack(text: str):
    if not text.startswith(PACKED):
        raise ValueError("not a packed value")
    return json.loads(zlib.decompress(base64.b64decode(text[len(PACKED):], validate=True)))


class PlanningInputs:
    def __init__(self, kv: KeyValue, source: PlanningSource, reach: ReachSource,
                 prefix: str = "genie:"):
        self.kv, self.source, self.reach_source, self.prefix = kv, source, reach, prefix

    async def availability(self, token, owner: str, client_id: str, q: PlanQuery) -> Availability:
        key = f"{self.prefix}plan:free:{owner}:{client_id}:{hashlib.sha256(q.key().encode()).hexdigest()}"
        return await self._cached(key, FREE_TTL_S, lambda d: parse_availability(d, q),
                                  lambda: self.source.availability(token, client_id, q))

    async def rate_cards(self, token, owner: str, client_id: str, brand_id: str) -> RateCard:
        key = f"{self.prefix}plan:rates:{owner}:{client_id}"
        return await self._cached(key, RATES_TTL_S,
                                  lambda d: parse_rate_cards(d, client_id, brand_id),
                                  lambda: self.source.rate_cards(token, client_id))

    async def reach(self, token, owner: str, client_id: str) -> Reach:
        key = f"{self.prefix}plan:reach:{owner}:{client_id}"
        return await self._cached(key, REACH_TTL_S, parse_reach,
                                  lambda: self.reach_source.reach(token, client_id))

    async def quote(self, token, client_id: str, brand_id: str, q: PlanQuery,
                    selections: list[dict]) -> Decimal:
        """The exact brand price of one plan. Never cached: it is the number the user is
        shown as the price."""
        return parse_quote(await self.source.quote(token, client_id, brand_id, q, selections))

    async def _cached(self, key: str, ttl: int, parse, fetch):
        raw = await self.kv.get(key)
        if raw is not None:
            try:
                return parse(unpack(raw))
            except (ValueError, binascii.Error, zlib.error, ContractError) as err:
                log.warning("planning cache value refused (%s) - reading fresh", type(err).__name__)
                await self.kv.delete(key)
        data = await fetch()
        parsed = parse(data)                                  # raises ContractError
        await self.kv.set(key, pack(data), ttl)
        return parsed
