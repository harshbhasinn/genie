"""A small key-value store for Genie's short-lived state: chat sessions, the `me` cache,
and later the calculator's caches. Two kinds behind one interface:

  MemoryKV   this process's memory - local runs and tests; nothing shared between processes
  RedisKV    Redis - shared by every Genie task, which is what lets the 1+1 ECS tasks
             share sessions and survive a Spot task being reclaimed

Every value is written with a TTL, so nothing piles up. Redis failing never fails a
request: a read returns nothing and a write is skipped, and the error is counted for
/healthz. Genie then loses follow-up context for that turn, never the answer.

Isolation is not this module's job - it stores what it is given under the key it is
given. Keys carry their owner (see chat/session.py), so one user's key is never another's.
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Protocol

log = logging.getLogger(__name__)


class KeyValue(Protocol):
    kind: str

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_s: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def health(self) -> dict: ...

    async def close(self) -> None: ...


class MemoryKV:
    """Bounded: past `max_items`, the least recently used key goes first."""
    kind = "memory"

    def __init__(self, max_items: int = 5000, clock=time.monotonic):
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._max = max_items
        self._clock = clock

    async def get(self, key: str) -> str | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= self._clock():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        self._items[key] = (self._clock() + ttl_s, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    async def delete(self, key: str) -> None:
        self._items.pop(key, None)

    async def health(self) -> dict:
        return {"kind": self.kind, "items": len(self._items)}

    async def close(self) -> None:
        pass


class RedisKV:
    """Short socket timeouts: a slow Redis must cost a turn its context, not its answer."""
    kind = "redis"

    def __init__(self, url: str, timeout_s: float = 0.5, client=None):
        if client is None:
            import redis.asyncio as redis          # only needed when Redis is configured
            client = redis.from_url(url, socket_timeout=timeout_s,
                                    socket_connect_timeout=timeout_s, decode_responses=True)
        self._redis = client
        self.errors = 0

    def _failed(self, op: str, err: Exception) -> None:
        self.errors += 1
        log.warning("redis %s failed (%s) - carrying on without it", op, type(err).__name__)

    async def get(self, key: str) -> str | None:
        try:
            return await self._redis.get(key)
        except Exception as err:  # noqa: BLE001 - any Redis failure degrades, never raises
            self._failed("get", err)
            return None

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        try:
            await self._redis.set(key, value, ex=ttl_s)
        except Exception as err:  # noqa: BLE001
            self._failed("set", err)

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception as err:  # noqa: BLE001
            self._failed("delete", err)

    async def health(self) -> dict:
        try:
            reachable = bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            reachable = False
        return {"kind": self.kind, "reachable": reachable, "errors": self.errors}

    async def close(self) -> None:
        try:
            await self._redis.aclose()
        except Exception:  # noqa: BLE001
            pass


def build(redis_url: str) -> KeyValue:
    """Redis when a URL is configured; this process's memory otherwise."""
    return RedisKV(redis_url) if redis_url else MemoryKV()
