"""The isolation and sharing rules against a REAL Redis.

Skipped unless GENIE_TEST_REDIS_URL is set - CI has no Redis; locally:
    docker compose up -d redis
    GENIE_TEST_REDIS_URL=redis://:genie_local_dev@127.0.0.1:6381/0 python -m pytest tests/test_sessions_redis.py

Two SessionStores with their OWN connections stand in for the two ECS tasks. Each test
writes under its own random prefix and deletes everything it wrote.
"""
from __future__ import annotations

import hashlib
import os
import uuid

import pytest

from genie.chat.session import SessionStore
from genie.ontology import load
from genie.store import RedisKV
from .test_sessions import REPO, TOKEN_A, TOKEN_B, serve, turn

URL = os.environ.get("GENIE_TEST_REDIS_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="GENIE_TEST_REDIS_URL is not set")


@pytest.fixture(scope="module")
def ontology():
    return load(REPO)


@pytest.fixture
def prefix():
    import redis
    p = f"genie-test-{uuid.uuid4().hex[:8]}:"
    yield p
    r = redis.Redis.from_url(URL, decode_responses=True)
    for key in r.scan_iter(f"{p}*"):
        r.delete(key)


def task(prefix: str) -> SessionStore:
    """One 'ECS task': its own Redis connection, the shared server."""
    return SessionStore(RedisKV(URL), prefix=prefix)


def test_two_tasks_share_one_conversation_and_still_isolate_users(ontology, prefix):
    task_1, task_2 = task(prefix), task(prefix)
    with serve(ontology, task_1) as (c, _):
        sid_a, _, asked = turn(c, TOKEN_A, "what is my wallet balance")          # on task 1
        assert asked == "Which balance do you mean?"
        from genie.main import app
        app.state.sessions = task_2                                              # next turn: task 2
        sid_b, routed_b, _ = turn(c, TOKEN_B, "Main wallet", sid_a)
        assert sid_b != sid_a and routed_b != "follow_up"                        # B: nothing of A's
        again, routed, answer = turn(c, TOKEN_A, "Main wallet", sid_a)
        assert (again, routed) == (sid_a, "follow_up") and "₹15,75,000" in answer  # A: continued
        health = c.get("/healthz").json()["sessions"]
    assert health == {"kind": "redis", "reachable": True, "errors": 0}


def test_keys_carry_their_owner_expire_and_never_hold_a_token(ontology, prefix):
    import redis
    with serve(ontology, task(prefix)) as (c, _):
        sid_a, _, _ = turn(c, TOKEN_A, "what is my wallet balance")
        sid_b, _, _ = turn(c, TOKEN_B, "what is my wallet balance")
    r = redis.Redis.from_url(URL, decode_responses=True)
    keys = sorted(r.scan_iter(f"{prefix}*"))
    assert keys == sorted([
        f"{prefix}session:user:u-a:{sid_a}", f"{prefix}session:user:u-b:{sid_b}",
        f"{prefix}me:{hashlib.sha256(TOKEN_A.encode()).hexdigest()}",
        f"{prefix}me:{hashlib.sha256(TOKEN_B.encode()).hexdigest()}"])
    for key in keys:
        assert 0 < r.ttl(key) <= 3600                                            # everything expires
        assert TOKEN_A not in r.get(key) and TOKEN_B not in r.get(key)          # no token stored
    assert r.ttl(f"{prefix}me:{hashlib.sha256(TOKEN_A.encode()).hexdigest()}") <= 300
