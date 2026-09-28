#!/usr/bin/env python3
"""Validate the Genie capability catalog.

  python tools/validate_catalog.py            schema + ontology cross-refs + follow-up integrity
  python tools/validate_catalog.py --strict   also fail on anything not yet verified

Exit 0 = clean, 1 = errors. `blocked` capabilities are reported, never an error.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    import yaml
    from jsonschema import Draft202012Validator
except ImportError:
    sys.exit("needs deps:  pip install -r tools/requirements.txt")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "catalog"
ONT = ROOT / "ontology"
SCHEMA = CAT / "schema" / "capability.schema.json"


def load_ontology() -> tuple[set[str], set[str]]:
    """Return (metric ids, screen ids) so the catalog can be cross-checked against them."""
    metrics: set[str] = set()
    screens: set[str] = set()
    for p in (ONT / "screens").glob("*.yaml"):
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        s = doc.get("screen") or {}
        if s.get("id"):
            screens.add(s["id"])
        for m in s.get("metrics") or []:
            if m.get("id"):
                metrics.add(m["id"])
    return metrics, screens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    if not SCHEMA.exists():
        print(f"ERROR: schema not found at {SCHEMA}")
        return 1

    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    files = sorted(p for p in CAT.glob("*.yaml"))
    if not files:
        print("ERROR: no capability files in catalog/")
        return 1

    ont_metrics, ont_screens = load_ontology()

    errors: list[str] = []
    warnings: list[str] = []
    status_counts: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    followup_counts: Counter[str] = Counter()
    blocked_by: Counter[str] = Counter()
    seen_ids: set[str] = set()
    utterance_owner: dict[str, str] = {}
    otp_gated = 0

    for path in files:
        rel = path.relative_to(ROOT)
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
            loc = "/".join(str(x) for x in err.path) or "(root)"
            errors.append(f"{rel}: {loc}: {err.message}")
        if "capabilities" not in doc:
            continue

        domain = doc.get("domain", "?")
        for c in doc["capabilities"]:
            cid = c.get("id", "?")
            where = f"{rel}: '{cid}'"
            st = c.get("status", "?")
            status_counts[st] += 1
            mode_counts[c.get("genie_mode", "?")] += 1
            if c.get("otp_gated"):
                otp_gated += 1
            if st == "blocked":
                for b in c.get("blocked_by") or []:
                    blocked_by[b] += 1

            if cid in seen_ids:
                errors.append(f"{where}: duplicate capability id.")
            seen_ids.add(cid)

            if "." in cid and cid.split(".")[0] != domain:
                errors.append(
                    f"{where}: id prefix does not match the file's domain '{domain}'.")

            # an OTP-gated capability must never claim it can act
            if c.get("otp_gated") and c.get("genie_mode") == "prefill":
                errors.append(
                    f"{where}: otp_gated with genie_mode=prefill. Genie cannot complete an OTP.")
            if c.get("mutating") and c.get("genie_mode") == "answer":
                errors.append(f"{where}: mutating capability with genie_mode=answer.")

            # cross-reference the ontology
            for mid in c.get("answers_with") or []:
                if mid not in ont_metrics:
                    errors.append(f"{where}: answers_with '{mid}' is not an ontology metric.")
            tgt = c.get("target_screen")
            if tgt and tgt not in ont_screens:
                errors.append(f"{where}: target_screen '{tgt}' is not an ontology screen.")

            # utterances must be distinctive — a phrase owned by two capabilities is a
            # selection collision waiting to happen
            for u in c.get("utterances") or []:
                key = " ".join(u.lower().split())
                if key in utterance_owner and utterance_owner[key] != cid:
                    errors.append(
                        f"{where}: utterance '{u}' is also claimed by "
                        f"'{utterance_owner[key]}'.")
                utterance_owner[key] = cid

            # follow-ups
            for f in c.get("follow_ups") or []:
                fid, kind = f.get("id", "?"), f.get("kind", "?")
                fw = f"{where} follow-up '{fid}'"
                followup_counts[kind] += 1

                opts = f.get("options") or []
                if kind in ("disambiguation", "scope") and len(opts) < 2:
                    errors.append(f"{fw}: {kind} needs at least two options.")
                if kind == "entity_choice" and not f.get("options_from") and not opts:
                    errors.append(
                        f"{fw}: entity_choice needs options_from (a live source) or options.")

                for o in opts:
                    if not (o.get("hint") or "").strip():
                        errors.append(f"{fw}: option '{o.get('value')}' has no hint.")
                    # a disambiguation option naming a metric must name a real one
                    v = o.get("value", "")
                    if kind == "disambiguation" and v not in ont_metrics \
                            and v in {x.split(".")[-1] for x in ont_metrics}:
                        warnings.append(f"{fw}: option '{v}' looks like a metric but is not one.")

                d = f.get("default_after_asks")
                if d and opts and d not in [o.get("value") for o in opts]:
                    errors.append(
                        f"{fw}: default_after_asks '{d}' is not one of the offered options.")

                for sid in f.get("resolved_by_context") or []:
                    if sid not in ont_screens:
                        errors.append(
                            f"{fw}: resolved_by_context '{sid}' is not an ontology screen.")

                if not f.get("resolved_by_context"):
                    warnings.append(
                        f"{fw}: no resolved_by_context — Genie will ask even when the screen "
                        "already answers it.")

            if st == "verified" and not c.get("verified_against"):
                errors.append(f"{where}: verified without verified_against.")
            if st in ("pending_verification", "pending_transcription"):
                warnings.append(f"{where}: {st}")

    # ---- report ----
    total = sum(status_counts.values())
    print(f"\nCatalog: {len(files)} domain file(s), {total} capabilities\n")
    for st in ("verified", "pending_verification", "blocked", "pending_transcription"):
        n = status_counts.get(st, 0)
        if n:
            flag = "  ← offered to users" if st == "verified" else ""
            print(f"  {st:<24} {n:>3}  {'█' * min(n, 40)}{flag}")

    if mode_counts:
        print("\nBy mode:")
        for m, n in sorted(mode_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {m:<12} {n:>3}")
        print(f"  (otp_gated: {otp_gated} — Genie can never complete these)")

    if followup_counts:
        tot = sum(followup_counts.values())
        print(f"\nFollow-ups: {tot}")
        for k, n in sorted(followup_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {k:<18} {n:>3}")

    if blocked_by:
        print("\nBlocked by ruling:")
        for r, n in sorted(blocked_by.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {r}: {n}")

    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings[:30]:
            print(f"  · {w}")
        if len(warnings) > 30:
            print(f"  … and {len(warnings) - 30} more")

    if errors:
        print(f"\n{len(errors)} ERROR(s):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print(f"\nSchema, ontology cross-references and follow-ups clean. "
          f"{status_counts.get('verified', 0)}/{total} offered.")

    if args.strict:
        unready = total - status_counts.get("verified", 0) - status_counts.get("blocked", 0)
        if unready:
            print(f"\n--strict: {unready} capability(ies) not yet verified.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
