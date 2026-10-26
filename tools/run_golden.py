#!/usr/bin/env python3
"""Score the router against tests/golden-queries.yaml — S3's exit gate.

    python tools/run_golden.py                 # exact + lexical only, no network
    python tools/run_golden.py --llm           # full ladder, costs tokens
    python tools/run_golden.py --only g001,g002
    python tools/run_golden.py --failures      # only show what missed

Scores three things separately, because they fail for different reasons:

  CAPABILITY   did it route to the right one
  FOLLOW-UP    did the right question fire, or correctly not fire
  MODE         answer / navigate / handoff / plan / prefill

Routing to the right capability and then asking the wrong question is a
different bug from routing wrong, and averaging them into one number hides both.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "service"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from genie.config import settings  # noqa: E402
from genie.llm import build as build_provider  # noqa: E402
from genie.ontology import load  # noqa: E402
from genie.router.embeddings import CapabilityIndex  # noqa: E402
from genie.router.followups import decide  # noqa: E402
from genie.router.intent import IntentRouter  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true",
                    help="enable the vector and model rungs (costs tokens)")
    ap.add_argument("--only", help="comma-separated query ids")
    ap.add_argument("--failures", action="store_true", help="only print misses")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    cfg = settings()
    ontology = load(ROOT)
    doc = yaml.safe_load((ROOT / "tests" / "golden-queries.yaml").read_text(encoding="utf-8"))
    queries = doc["queries"]
    if args.only:
        wanted = {q.strip() for q in args.only.split(",")}
        queries = [q for q in queries if q["id"] in wanted]
    if args.limit:
        queries = queries[: args.limit]

    provider = build_provider(cfg) if args.llm else None
    index = None
    if provider is not None:
        utterances = {u: c.id for c in ontology.capabilities.values()
                      for u in c.utterances}
        index = CapabilityIndex(utterances, cfg.genie_embed_model)
        await index.build(provider, ROOT / ".cache")

    # A degraded ladder scores lower for reasons that have nothing to do with
    # the code under test. That has now silently invalidated several rounds of
    # tuning — twice from a provider spend cap, once from a batch-size 400 — so
    # it is probed up front and shouted about, rather than left to be inferred
    # from a disappointing number.
    degraded = None
    if provider is not None:
        if index is None or not index.ready:
            degraded = "the capability index did not build"
        elif not await index.search(provider, "wallet balance", limit=1):
            degraded = "query embedding is failing — spend cap or quota"
    if degraded:
        bar = "!" * 64
        print("")
        print(RED + bar)
        print("  LADDER DEGRADED - " + degraded + ".")
        print("  Vector and model rungs are OFF. These scores are NOT")
        print("  comparable with a full run, and must not be tuned against.")
        print(bar + RESET)
        await provider.close()
        provider = None


    router = IntentRouter(ontology, provider, index)
    rungs = "exact + lexical + vector + model" if provider else "exact + lexical"
    print(f"\n{len(queries)} queries · ladder: {rungs}\n")

    cap_ok = fu_ok = mode_ok = 0
    by_method: Counter[str] = Counter()
    misses: list[str] = []

    try:
        for q in queries:
            ctx = q.get("context") or {}
            exp = q.get("expect") or {}
            role = ctx.get("role")
            route = await router.route(q["query"], screen=ctx.get("screen"),
                                       roles=frozenset({role} if role else ()))
            by_method[route.method] += 1

            got_cap = route.capability.id if route.routed else None
            want_cap = exp.get("capability")
            cap_hit = got_cap == want_cap
            cap_ok += cap_hit

            got_mode = route.capability.mode.value if route.routed else None
            mode_hit = got_mode == exp.get("mode")
            mode_ok += mode_hit

            want_fu = exp.get("follow_up")
            got_fu = None
            if route.routed:
                ask = decide(route.capability, screen=ctx.get("screen"),
                             query=q["query"], ontology=ontology)
                got_fu = ask.follow_up.id if ask else None
            fu_hit = got_fu == want_fu
            fu_ok += fu_hit

            if cap_hit and fu_hit and mode_hit:
                if not args.failures:
                    print(f"  {GREEN}pass{RESET} {q['id']}  {DIM}{q['query'][:56]}{RESET}"
                          f"  {DIM}[{route.method} {route.confidence:.2f}]{RESET}")
                continue

            detail = []
            if not cap_hit:
                detail.append(f"capability: want {want_cap}, got {got_cap or '—'}")
            if not fu_hit:
                detail.append(f"follow-up: want {want_fu or 'none'}, got {got_fu or 'none'}")
            if not mode_hit and cap_hit:
                detail.append(f"mode: want {exp.get('mode')}, got {got_mode}")
            line = (f"  {RED}FAIL{RESET} {q['id']}  {q['query'][:56]}\n"
                    f"        {YELLOW}{' · '.join(detail)}{RESET}")
            if route.candidates:
                line += (f"\n        {DIM}candidates: "
                         + ", ".join(f"{c.capability.id}@{c.score:.2f}"
                                     for c in route.candidates[:4]) + RESET)
            print(line)
            misses.append(q["id"])
    finally:
        if provider is not None:
            await provider.close()

    n = len(queries)
    print(f"\n{'─' * 62}")
    print(f"  capability   {cap_ok:3}/{n}   {cap_ok / n:6.1%}")
    print(f"  follow-up    {fu_ok:3}/{n}   {fu_ok / n:6.1%}")
    print(f"  mode         {mode_ok:3}/{n}   {mode_ok / n:6.1%}")
    print(f"  all three    {n - len(misses):3}/{n}   {(n - len(misses)) / n:6.1%}")
    print(f"\n  routed via: " + " · ".join(f"{k} {v}" for k, v in by_method.most_common()))
    if misses:
        print(f"\n  failing: {' '.join(misses)}")
    return 0 if not misses else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
