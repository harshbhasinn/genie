#!/usr/bin/env python3
"""Where the catalog and the golden set contradict each other.

Both were authored by hand, at different times, from different angles: the
catalog domain by domain ("what should wallet.* respond to"), the golden set
behaviour by behaviour ("what should THIS query do"). They were never
reconciled, and the router cannot satisfy both.

This separates failures the router can fix from failures that need a ruling:

  UTTERANCE CLAIM   the golden query IS an authored utterance of a DIFFERENT
                    capability. No routing logic can win — one of the two files
                    is wrong.
  CONTEXT SUPPRESS  the golden set expects a follow-up that the catalog says is
                    resolved by that very screen.
  ROUTER            genuine matching weakness. Ours to fix.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "service"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from genie.ontology import load  # noqa: E402

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def main() -> int:
    ontology = load(ROOT)
    doc = yaml.safe_load((ROOT / "tests" / "golden-queries.yaml").read_text(encoding="utf-8"))

    owner = {u.strip().lower(): c.id
             for c in ontology.capabilities.values() for u in c.utterances}

    buckets: dict[str, list[str]] = defaultdict(list)
    for q in doc["queries"]:
        exp = q.get("expect") or {}
        want_cap = exp.get("capability")
        want_fu = exp.get("follow_up")
        screen = (q.get("context") or {}).get("screen")
        qid, text = q["id"], q["query"]

        claimed = owner.get(text.strip().lower())
        if claimed and claimed != want_cap:
            buckets["UTTERANCE CLAIM"].append(
                f"{qid}  \"{text}\"\n        catalog gives this exact phrase to "
                f"{BOLD}{claimed}{RESET}, golden expects {BOLD}{want_cap}{RESET}")
            continue

        if want_fu and want_cap:
            cap = ontology.capability(want_cap)
            if cap:
                fu = next((f for f in cap.follow_ups if f.id == want_fu), None)
                if fu is None:
                    buckets["MISSING FOLLOW-UP"].append(
                        f"{qid}  expects {want_fu}, which {want_cap} does not have")
                elif fu.suppressed_on(screen):
                    buckets["CONTEXT SUPPRESS"].append(
                        f"{qid}  \"{text}\"\n        golden expects {BOLD}{want_fu}{RESET} "
                        f"on {screen}, but the catalog lists {screen} in its "
                        f"resolved_by_context")

    total = sum(len(v) for v in buckets.values())
    print(f"\n{total} of {len(doc['queries'])} golden queries contradict the catalog\n")
    for kind in ("UTTERANCE CLAIM", "CONTEXT SUPPRESS", "MISSING FOLLOW-UP"):
        rows = buckets.get(kind)
        if not rows:
            continue
        print(f"{BOLD}── {kind} ({len(rows)}) ──{RESET}")
        for row in rows:
            print(f"  {row}")
        print()

    print(f"{DIM}Everything not listed here is the router's problem, not a ruling.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
