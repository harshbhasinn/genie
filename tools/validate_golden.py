#!/usr/bin/env python3
"""Cross-check the golden query set against the ontology and the catalog.

The golden set is only worth having if it breaks when the thing it describes
changes. This checks every id in it actually exists, every expectation is
self-consistent, and reports which capabilities no query covers.

    python tools/validate_golden.py
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden-queries.yaml"
SCREENS = ROOT / "ontology" / "screens"
CATALOG = ROOT / "catalog"

GAP_KINDS = {"unserved", "wrong_grain", "freshness", "suppressed",
             "withheld", "permission", "isolation", "out_of_scope"}

ROLES = {"ADOPS_ADMIN", "AD_PARTNER_ADMIN", "NETWORK_PARTNER_ADMIN", "CAMPAIGN_MANAGER",
         "BRAND_MANAGER", "HARDWARE_MANAGER", "KELLY_WORKER", "STATION_WORKER"}


def load_world():
    screens, metrics, blocked = set(), set(), set()
    for path in sorted(SCREENS.glob("*.yaml")):
        screen = yaml.safe_load(path.read_text(encoding="utf-8"))["screen"]
        screens.add(screen["id"])
        for metric in screen.get("metrics") or []:
            metrics.add(metric["id"])
            if metric.get("status") == "blocked":
                blocked.add(metric["id"])

    caps = {}
    for path in sorted(CATALOG.glob("*.yaml")):
        for cap in yaml.safe_load(path.read_text(encoding="utf-8"))["capabilities"]:
            caps[cap["id"]] = {
                "mode": cap.get("genie_mode"),
                "otp": bool(cap.get("otp_gated")),
                "mutating": bool(cap.get("mutating")),
                "follow_ups": {f["id"] for f in (cap.get("follow_ups") or [])},
            }
    return screens, metrics, blocked, caps


def main():
    doc = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    queries = doc.get("queries") or []
    screens, metrics, blocked, caps = load_world()

    errors, warnings = [], []
    seen_ids, seen_text = Counter(), defaultdict(list)
    covered = set()
    tally = Counter()
    gap_tally = Counter()

    for q in queries:
        qid = q.get("id", "?")
        seen_ids[qid] += 1
        text = (q.get("query") or "").strip().lower()
        seen_text[text].append(qid)
        ctx = q.get("context") or {}
        exp = q.get("expect") or {}

        if not q.get("why", "").strip():
            errors.append(f"{qid}: no `why` — a query with no distinct reason to exist does not belong")

        screen = ctx.get("screen")
        if screen not in screens:
            errors.append(f"{qid}: screen '{screen}' is not in the ontology")
        role = ctx.get("role")
        if role not in ROLES:
            errors.append(f"{qid}: role '{role}' is not a known role")

        cid = exp.get("capability")
        if cid not in caps:
            errors.append(f"{qid}: capability '{cid}' is not in the catalog")
            continue
        cap = caps[cid]
        covered.add(cid)

        if exp.get("mode") != cap["mode"]:
            errors.append(f"{qid}: expects mode '{exp.get('mode')}' but "
                          f"{cid} is genie_mode '{cap['mode']}'")

        fu = exp.get("follow_up")
        if fu is not None and fu not in cap["follow_ups"]:
            have = ", ".join(sorted(cap["follow_ups"])) or "none"
            errors.append(f"{qid}: follow-up '{fu}' is not on {cid} (has: {have})")

        for mid in exp.get("metrics") or []:
            if mid not in metrics:
                errors.append(f"{qid}: metric '{mid}' is not in the ontology")

        # A query built only from blocked metrics must be flagged as an honest gap,
        # or the test set is asserting Genie can answer something nothing serves.
        mids = exp.get("metrics") or []
        unavail = exp.get("unavailable") or []
        for mid in unavail:
            if mid not in metrics:
                errors.append(f"{qid}: unavailable metric '{mid}' is not in the ontology")
            elif mid not in blocked:
                errors.append(f"{qid}: lists '{mid}' as unavailable but it is not blocked")
        if mids and all(m in blocked for m in mids) and not exp.get("honest_gap"):
            errors.append(f"{qid}: every metric is blocked but honest_gap is not set — "
                          f"this query cannot be answered")

        kind = exp.get("gap_kind")
        if exp.get("honest_gap"):
            if kind not in GAP_KINDS:
                errors.append(f"{qid}: honest_gap needs a gap_kind from "
                              f"{', '.join(sorted(GAP_KINDS))} — 'I don't have that' has "
                              f"several meanings and they need different answers")
            elif kind == "unserved" and not any(m in blocked for m in mids + unavail):
                warnings.append(f"{qid}: gap_kind 'unserved' but no metric named is blocked — "
                                f"name the blocked metric under `unavailable`")
        elif kind:
            errors.append(f"{qid}: gap_kind set without honest_gap")

        # A mutating capability must be a refusal. Genie holds no write paths.
        if cap["mutating"] and not exp.get("refuses"):
            errors.append(f"{qid}: {cid} is mutating but the query does not expect a refusal")
        if exp.get("refuses") and not cap["mutating"] and cid not in (
                "explain.capabilities", "explain.why_unavailable"):
            warnings.append(f"{qid}: expects a refusal from the non-mutating {cid}")

        if exp.get("gap_kind"):
            gap_tally[exp["gap_kind"]] += 1
        tally["refusal" if exp.get("refuses") else
              "honest gap" if exp.get("honest_gap") else
              "follow-up" if fu else "direct"] += 1

    for qid, n in seen_ids.items():
        if n > 1:
            errors.append(f"duplicate query id '{qid}' ({n} times)")
    for text, ids in seen_text.items():
        if len(ids) > 1 and len({tuple(sorted((yaml.safe_dump(q.get('context')),)))
                                 for q in queries if (q.get('query') or '').strip().lower() == text}) < len(ids):
            pass  # same text with different context is the point — see g001/g002

    print(f"\nGolden set: {len(queries)} query(s) across {len(covered)} capability(s)\n")
    for kind in ("direct", "follow-up", "refusal", "honest gap"):
        if tally[kind]:
            bar = "█" * min(40, tally[kind])
            print(f"  {kind:12} {tally[kind]:3}  {bar}")

    if gap_tally:
        print("\n  gap kinds: " + " · ".join(f"{k} {n}" for k, n in gap_tally.most_common()))

    ctx_screens = Counter(q.get("context", {}).get("screen") for q in queries)
    ctx_roles = Counter(q.get("context", {}).get("role") for q in queries)
    print(f"\n  screens exercised: {len(ctx_screens)} of {len(screens)}")
    print("  roles exercised:   " + ", ".join(f"{r} ({n})" for r, n in ctx_roles.most_common()))

    uncovered = sorted(set(caps) - covered)
    if uncovered:
        print(f"\n{len(uncovered)} capability(s) with no golden query:")
        for cid in uncovered:
            print(f"  · {cid}  ({caps[cid]['mode']})")

    unseen = sorted(s for s in screens if s not in ctx_screens)
    if unseen:
        print("\nScreens never used as context: " + ", ".join(unseen))

    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f"  · {w}")

    if errors:
        print(f"\n{len(errors)} ERROR(s):")
        for e in errors:
            print(f"  x {e}")
        return 1

    print("\nEvery id resolves. Modes, follow-ups, metrics and refusals are consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
