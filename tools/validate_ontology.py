#!/usr/bin/env python3
"""Validate the Dashboard Ontology.

  python tools/validate_ontology.py            schema + cross-refs + coverage report
  python tools/validate_ontology.py --strict   also fail on pending_verification (S1 exit gate)

Exit 0 = clean. Exit 1 = errors. `blocked` metrics are reported, never an error:
a blocked metric means we are waiting on a human ruling, not that we skipped the work.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("needs pyyaml:  pip install pyyaml jsonschema")
try:
    from jsonschema import Draft202012Validator
except ImportError:
    sys.exit("needs jsonschema:  pip install pyyaml jsonschema")

# Windows consoles default to cp1252; CI may pipe to a non-tty. Never let output encoding
# be the reason a validation run fails.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
ONT = ROOT / "ontology"
SCHEMA = ONT / "schema" / "screen.schema.json"

SERVED = {"verified"}
BLOCKING_RULINGS = {"R1", "R2", "R3", "R4", "R5", "R6", "R7"}


def load_screens() -> list[tuple[Path, dict]]:
    out = []
    for p in sorted((ONT / "screens").glob("*.yaml")):
        with p.open(encoding="utf-8") as fh:
            out.append((p, yaml.safe_load(fh)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="fail on pending_verification / pending_transcription")
    args = ap.parse_args()

    if not SCHEMA.exists():
        print(f"ERROR: schema not found at {SCHEMA}")
        return 1

    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    screens = load_screens()
    if not screens:
        print("ERROR: no screens found in ontology/screens/")
        return 1

    errors: list[str] = []
    warnings: list[str] = []
    status_counts: Counter[str] = Counter()
    field_counts: Counter[str] = Counter()
    prefillable = 0
    blocked_by: Counter[str] = Counter()
    seen_screen_ids: set[str] = set()
    all_metric_ids: set[str] = set()

    for path, doc in screens:
        rel = path.relative_to(ROOT)

        # 1. schema
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
            loc = "/".join(str(x) for x in err.path) or "(root)"
            errors.append(f"{rel}: {loc}: {err.message}")
        if not doc or "screen" not in doc:
            continue

        s = doc["screen"]
        sid = s.get("id", "?")
        if sid in seen_screen_ids:
            errors.append(f"{rel}: duplicate screen id '{sid}'")
        seen_screen_ids.add(sid)

        metrics = s.get("metrics") or []
        ids = [m["id"] for m in metrics if "id" in m]
        for mid, n in Counter(ids).items():
            if n > 1:
                errors.append(f"{rel}: duplicate metric id '{mid}' ({n}x)")
        all_metric_ids.update(f"{sid}.{i}" for i in ids)
        idset = set(ids)

        # 2. sections reference real metrics
        for sec in s.get("sections") or []:
            for mid in sec.get("metrics") or []:
                if mid not in idset:
                    errors.append(
                        f"{rel}: section '{sec['id']}' references unknown metric '{mid}'")

        # 3. per-metric rules the JSON Schema cannot express
        for m in metrics:
            mid, st = m.get("id", "?"), m.get("status", "?")
            status_counts[st] += 1
            where = f"{rel}: metric '{mid}'"

            if st == "blocked":
                for b in m.get("blocked_by") or []:
                    blocked_by[b] += 1

            if st == "verified":
                src = m.get("source") or {}
                if src.get("scope_key") in (None, "unknown"):
                    errors.append(
                        f"{where}: status=verified but scope_key is unknown. "
                        "owner_id vs client_id decides whose data this is.")
                if m.get("freshness") in (None, "unknown"):
                    errors.append(
                        f"{where}: status=verified but freshness is unknown. "
                        "Genie cannot stamp as_of without it.")

            if m.get("camera_measured") and m.get("freshness") == "live":
                errors.append(
                    f"{where}: camera_measured metrics come from the ~6h batch, not live.")

            if m.get("deck_value") and st == "verified" and not m.get("verified_against"):
                errors.append(f"{where}: verified without verified_against.")

            if st in ("pending_verification", "pending_transcription"):
                warnings.append(f"{where}: {st}")

        # 4. actions
        for a in s.get("actions") or []:
            cap, st = a.get("capability", "?"), a.get("status", "?")
            where = f"{rel}: action '{cap}'"
            if a.get("otp_gated") and a.get("genie_mode") == "direct":
                errors.append(
                    f"{where}: otp_gated actions can never be genie_mode=direct. "
                    "Genie cannot complete an OTP.")
            if a.get("mutating") and a.get("genie_mode") == "direct":
                warnings.append(
                    f"{where}: mutating + genie_mode=direct. The design is prefill-only "
                    "(FINDINGS.md §5) — confirm this is deliberate.")
            if st == "verified" and not a.get("verified_against"):
                errors.append(f"{where}: verified without verified_against.")

        # 5. wizard steps and form fields — what S8's prefill maps onto
        variants = set(s.get("variants") or [])
        seen_steps: set[tuple] = set()
        for stp in s.get("steps") or []:
            sid_ = stp.get("id", "?")
            applies = stp.get("applies_to", "both")
            where = f"{rel}: step '{sid_}'"
            key = (stp.get("index"), applies)
            if key in seen_steps:
                errors.append(
                    f"{where}: duplicate index {stp.get('index')} for variant '{applies}'.")
            seen_steps.add(key)
            if variants and applies != "both" and applies not in variants:
                errors.append(
                    f"{where}: applies_to '{applies}' is not in the screen's variants {sorted(variants)}.")

            fids = [f["id"] for f in (stp.get("fields") or []) if "id" in f]
            for fid, n in Counter(fids).items():
                if n > 1:
                    errors.append(f"{where}: duplicate field id '{fid}' ({n}x).")

            for f in stp.get("fields") or []:
                fid, fst = f.get("id", "?"), f.get("status", "?")
                field_counts[fst] += 1
                fwhere = f"{rel}: step '{sid_}' field '{fid}'"
                fapplies = f.get("applies_to", applies)
                if variants and fapplies != "both" and fapplies not in variants:
                    errors.append(
                        f"{fwhere}: applies_to '{fapplies}' is not in the screen's variants.")
                if f.get("prefillable"):
                    prefillable += 1
                    if fst == "verified" and not f.get("maps_to"):
                        errors.append(
                            f"{fwhere}: prefillable + verified but no maps_to. "
                            "Prefill cannot seed a field with no backend target.")
                if f.get("type") == "computed" and f.get("prefillable"):
                    errors.append(
                        f"{fwhere}: a computed field cannot be prefilled — it is derived.")
                if f.get("type") in ("enum", "multi_enum") and not f.get("options")                         and fst == "verified":
                    errors.append(f"{fwhere}: verified enum with no options listed.")
                if fst == "verified" and not f.get("verified_against"):
                    errors.append(f"{fwhere}: verified without verified_against.")

        # 6. related_screens resolve (after all ids collected — deferred below)
        doc["_rel"] = rel

    for path, doc in screens:
        if not doc or "screen" not in doc:
            continue
        for r in doc["screen"].get("related_screens") or []:
            if r not in seen_screen_ids:
                warnings.append(
                    f"{doc['_rel']}: related_screens → '{r}' not yet modelled")

    # ---- report ----
    total = sum(status_counts.values())
    print(f"\nOntology: {len(screens)} screen(s), {total} metric(s)\n")
    for st in ("verified", "pending_verification", "blocked", "pending_transcription"):
        n = status_counts.get(st, 0)
        if n:
            bar = "█" * min(n, 40)
            flag = "  ← served" if st in SERVED else ""
            print(f"  {st:<24} {n:>3}  {bar}{flag}")

    if field_counts:
        tot_f = sum(field_counts.values())
        print("")
        print(f"Form fields: {tot_f} across {len(screens)} screen(s) "
              f"— {prefillable} prefillable")
        for st in ("verified", "pending_verification", "blocked", "pending_transcription"):
            n = field_counts.get(st, 0)
            if n:
                print(f"  {st:<24} {n:>3}  {'█' * min(n, 40)}")

    if blocked_by:
        print("\nBlocked by ruling:")
        for r, n in sorted(blocked_by.items(), key=lambda kv: (-kv[1], kv[0])):
            tag = " (unblock first — highest impact)" if n == max(blocked_by.values()) else ""
            print(f"  {r}: {n} metric(s){tag}")

    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings[:40]:
            print(f"  · {w}")
        if len(warnings) > 40:
            print(f"  … and {len(warnings) - 40} more")

    if errors:
        print(f"\n{len(errors)} ERROR(s):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    served = status_counts.get("verified", 0)
    print(f"\nSchema and cross-references clean. {served}/{total} metric(s) servable.")

    if args.strict:
        unready = (status_counts.get("pending_verification", 0)
                   + status_counts.get("pending_transcription", 0))
        if unready:
            print(f"\n--strict: {unready} metric(s) still pending. S1's exit gate is zero "
                  f"pending; 'blocked' is allowed (it means a human owes us a ruling).")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
