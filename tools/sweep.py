#!/usr/bin/env python3
"""Run every mapped endpoint against a live BFF and report what actually came back.

This is the live half of VERIFY.md. The static half already ran; this is what
needs a token. One call per endpoint clears every metric under it, so the whole
ontology is ~23 calls rather than 94.

    python tools/sweep.py --token-file tok.txt --client-id <uuid>

Add --foreign-client-id to run the negative pass, which is the one people skip
and the one that matters most: a client you do NOT hold must return 403, never
an empty 200. An empty 200 renders as zero, and a zero that is really an
authorization failure is the worst answer Genie can give.

Stdlib only - no pip install on a locked-down QA box.
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_sources import load_spec, split_api, tokens  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "ontology" / "screens"
DEFAULT_BASE = "https://ag-dashboard-bff.qa.adgrid.ai"

# Path params we can discover from another call, and where from.
DISCOVER = {
    "campaignId": ("GET /api/v1/campaigns", ("campaigns", 0, "campaignId")),
    "deviceId": ("GET /api/v1/clients/{clientId}/devices/roster", ("devices", 0, "deviceId")),
    "userId": ("GET /api/v1/clients/{clientId}/members/wallet-overview", ("members", 0, "userId")),
    "managerId": ("GET /api/v1/clients/{clientId}/members/wallet-overview", ("members", 0, "userId")),
}


def call(base, path, method, token, body=None, timeout=30):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as err:
        raw = err.read().decode(errors="replace")
        try:
            return err.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return err.code, {"_raw": raw[:400]}
    except Exception as err:  # noqa: BLE001 - network, DNS, TLS all land here
        return 0, {"_error": str(err)[:300]}


def present_fields(node, out=None, depth=0):
    """Keys that are present AND non-null, so an absent field and a null one differ."""
    out = set() if out is None else out
    if depth > 6:
        return out
    if isinstance(node, dict):
        for key, val in node.items():
            if val is not None and val != [] and val != {}:
                out.add(key)
            present_fields(val, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:3]:
            present_fields(item, out, depth + 1)
    return out


def dig(payload, trail):
    node = payload.get("data", payload)
    for step in trail:
        if isinstance(step, int):
            if not isinstance(node, list) or len(node) <= step:
                return None
            node = node[step]
        else:
            if not isinstance(node, dict):
                return None
            node = node.get(step)
        if node is None:
            return None
    return node


def body_for(spec, method, path, ctx):
    """A minimal valid POST body from the spec's required fields."""
    if method != "POST":
        return None
    op = (spec.get("paths", {}).get(path) or {}).get("post") or {}
    content = (op.get("requestBody") or {}).get("content") or {}
    ref = ""
    for body in content.values():
        ref = (body.get("schema") or {}).get("$ref", "")
        if ref:
            break
    if not ref:
        return {}
    name = ref.split("/")[-1]
    schema = spec["components"]["schemas"].get(name, {})
    out = {}
    for field in schema.get("required", []):
        low = field.lower()
        if "clientid" in low or low in ("apid", "brandid"):
            out[field] = ctx["client_id"]
        elif low == "start" or "startdate" in low:
            out[field] = ctx["start"]
        elif low == "end" or "enddate" in low:
            out[field] = ctx["end"]
        else:
            prop = (schema.get("properties") or {}).get(field, {})
            kind = prop.get("type")
            out[field] = {"integer": 1, "number": 1, "boolean": False,
                          "array": [], "object": {}}.get(kind, "")
    return out


def collect():
    by_api = defaultdict(list)
    for path in sorted(SCREENS.glob("*.yaml")):
        screen = yaml.safe_load(path.read_text(encoding="utf-8"))["screen"]
        for metric in screen.get("metrics") or []:
            if metric.get("status") == "blocked":
                continue
            src = metric.get("source") or {}
            api = src.get("api")
            if not api or api.startswith("DERIVED"):
                continue
            by_api[api].append({"id": metric["id"], "screen": screen["id"],
                                "field": src.get("field") or ""})
    return by_api


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", required=True, help="file holding the raw JWT")
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--foreign-client-id", help="a client the token does NOT hold — runs the negative pass")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--start", default=str(date.today() - timedelta(days=14)))
    ap.add_argument("--end", default=str(date.today()))
    ap.add_argument("--out", default="sweep-results.json")
    args = ap.parse_args()

    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    spec, _ = load_spec()
    ctx = {"client_id": args.client_id, "start": args.start, "end": args.end}

    # ── who am I ────────────────────────────────────────────────────────
    status, me = call(args.base, "/api/v1/me", "GET", token)
    if status != 200:
        print(f"x  GET /api/v1/me returned {status}. The token is not usable — stop here.")
        print(f"   {json.dumps(me)[:300]}")
        return 1
    data = me.get("data", me)
    grants = data.get("grants") or []
    print(f"\nAuthenticated: {data.get('primaryRole')} · clientType {data.get('clientType')} "
          f"· {len(grants)} grant(s)")
    held = {g.get("clientId") for g in grants if isinstance(g, dict)}
    if held and args.client_id not in held:
        print(f"!  --client-id {args.client_id} is not in this token's grants. "
              f"Held: {', '.join(sorted(x for x in held if x))[:200]}")

    # ── discover path params ────────────────────────────────────────────
    found = {"clientId": args.client_id, "apId": args.client_id, "brandId": args.client_id}
    for param, (api, trail) in DISCOVER.items():
        method, path = split_api(api)
        status, payload = call(args.base, path.format(**found), method, token,
                               body_for(spec, method, path, ctx))
        value = dig(payload, trail) if status == 200 else None
        if value:
            found[param] = value
    missing = [p for p in DISCOVER if p not in found]
    if missing:
        print(f"!  could not discover: {', '.join(missing)} — those endpoints will be skipped")

    # ── the sweep ───────────────────────────────────────────────────────
    by_api = collect()
    results = []
    tally = defaultdict(int)
    print(f"\nSweeping {len(by_api)} endpoint(s) covering "
          f"{sum(len(v) for v in by_api.values())} metric(s)\n")

    for api in sorted(by_api):
        metrics = by_api[api]
        method, path = split_api(api)
        if not method:
            continue
        needed = re.findall(r"\{(\w+)\}", path)
        if any(n not in found for n in needed):
            print(f"  --  {api}\n      skipped — no {', '.join(n for n in needed if n not in found)}")
            tally["skipped"] += 1
            continue
        status, payload = call(args.base, path.format(**found), method, token,
                               body_for(spec, method, path, ctx))
        row = {"api": api, "status": status, "metrics": [m["id"] for m in metrics]}

        if status != 200:
            mark, note = "x", f"HTTP {status} — {json.dumps(payload)[:160]}"
            tally["failed"] += 1
        else:
            body = payload.get("data", payload)
            have = present_fields(body)
            row["suppressed"] = bool(isinstance(body, dict) and body.get("suppressed"))
            absent = []
            for metric in metrics:
                want = [t for t in tokens(metric["field"])]
                if want and not any(t in have for t in want):
                    absent.append(f"{metric['id']} ({', '.join(want[:2])})")
            row["absent"] = absent
            if row["suppressed"]:
                mark = "~"
                note = f"suppressed — {body.get('reason')}. Not a failure; not a verification either"
                tally["suppressed"] += 1
            elif absent:
                mark, note = "x", f"{len(absent)} field(s) absent or null: " + "; ".join(absent[:3])
                tally["fields missing"] += 1
            elif not have:
                mark, note = "~", "200 but the payload is empty — check this is really no data"
                tally["empty"] += 1
            else:
                mark, note = "ok", f"{len(metrics)} metric(s) confirmed"
                tally["verified"] += 1
        results.append(row)
        print(f"  {mark:3} {api}\n      {note}")

    # ── the negative pass ───────────────────────────────────────────────
    if args.foreign_client_id:
        print("\nNegative pass — a client this token does NOT hold must 403, never 200\n")
        alien = dict(found, clientId=args.foreign_client_id, apId=args.foreign_client_id,
                     brandId=args.foreign_client_id)
        alien_ctx = dict(ctx, client_id=args.foreign_client_id)
        leaks = 0
        for api in sorted(by_api):
            method, path = split_api(api)
            if not method or "clientId" not in path and "apId" not in path and "brandId" not in path:
                continue
            needed = re.findall(r"\{(\w+)\}", path)
            if any(n not in alien for n in needed):
                continue
            status, payload = call(args.base, path.format(**alien), method, token,
                                   body_for(spec, method, path, alien_ctx))
            if status in (401, 403):
                print(f"  ok  {api} → {status}")
            elif status == 200:
                body = payload.get("data", payload)
                shape = "EMPTY" if not present_fields(body) else "POPULATED"
                print(f"  x   {api} → 200 {shape}  ← must be 403")
                leaks += 1
            else:
                print(f"  ~   {api} → {status}")
        if leaks:
            print(f"\n  {leaks} endpoint(s) returned 200 for another tenant. "
                  f"An empty 200 reads as zero — this is a tenant-isolation defect, not a data gap.")

    print("\n" + " · ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")
    return 1 if tally["failed"] or tally["fields missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
