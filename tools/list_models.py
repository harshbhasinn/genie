#!/usr/bin/env python3
"""List the Gemini models this API key can actually reach, and prove the embedding width.

The console shows display names ("Gemini 2.5 Flash"); the API needs ids
("gemini-2.5-flash"). Reading them off the UI is how you get a 404 on the first
call. This asks the API instead, so the answer is true for THIS key on THIS day.

    python tools/list_models.py              # list what the key can reach
    python tools/list_models.py --probe      # also embed one string, report real dim

Stdlib only.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://generativelanguage.googleapis.com/v1beta"


def load_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
                # An empty value must fall through to the message below, not be
                # returned as a key — otherwise the API answers 403 and the real
                # problem ("you haven't filled it in") never reaches the reader.
                found = line.split("=", 1)[1].strip().strip("'\"")
                if found:
                    return found
                break
    sys.exit("GEMINI_API_KEY is empty.\n"
             f"  Edit {env} and set GEMINI_API_KEY=<your key from "
             "https://aistudio.google.com/apikey>")


def get(path: str, key: str, body: dict | None = None):
    url = f"{BASE}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("x-goog-api-key", key)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:400]}
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": str(e)[:300]}


def main() -> int:
    key = load_key()
    probe = "--probe" in sys.argv

    models, page = [], None
    while True:
        path = "models?pageSize=200" + (f"&pageToken={page}" if page else "")
        status, payload = get(path, key)
        if status != 200:
            print(f"x  ListModels returned {status}")
            print(f"   {json.dumps(payload)[:400]}")
            if status in (400, 403):
                print("\n   403/400 usually means the key is wrong, restricted to other APIs,\n"
                      "   or the Generative Language API is not enabled on that project.")
            return 1
        models.extend(payload.get("models", []))
        page = payload.get("nextPageToken")
        if not page:
            break

    gen, emb, other = [], [], []
    for m in models:
        methods = set(m.get("supportedGenerationMethods") or [])
        row = (m.get("name", "").removeprefix("models/"),
               m.get("displayName", ""),
               m.get("inputTokenLimit"), m.get("outputTokenLimit"))
        if methods & {"generateContent", "streamGenerateContent"}:
            gen.append(row)
        elif methods & {"embedContent", "batchEmbedContents"}:
            emb.append(row)
        else:
            other.append(row)

    print(f"\n{len(models)} model(s) reachable with this key\n")

    print("── GENERATION — put one of these in GENIE_LLM_MODEL ──")
    for name, disp, itok, otok in sorted(gen):
        lim = f"in {itok:,} / out {otok:,}" if itok and otok else ""
        print(f"  {name:44} {disp:32} {lim}")

    print("\n── EMBEDDING — put one of these in GENIE_EMBED_MODEL ──")
    for name, disp, itok, _ in sorted(emb):
        print(f"  {name:44} {disp:32} {f'in {itok:,}' if itok else ''}")

    if other:
        print(f"\n── other ({len(other)}) ──")
        for name, disp, *_ in sorted(other):
            print(f"  {name:44} {disp}")

    if probe and emb:
        print("\n── probing real embedding width ──")
        for name, *_ in sorted(emb):
            status, payload = get(f"models/{name}:embedContent", key, {
                "model": f"models/{name}",
                "content": {"parts": [{"text": "verified impressions"}]},
            })
            vals = ((payload.get("embedding") or {}).get("values")
                    or (payload.get("embedding") or {}).get("value") or [])
            if status == 200 and vals:
                print(f"  {name:44} dim = {len(vals)}")
            else:
                print(f"  {name:44} {status} {json.dumps(payload)[:120]}")
        print("\n  That dim is what the pgvector column must be. GENIE_EMBED_DIM=auto\n"
              "  asserts it at boot so a model change cannot silently corrupt the index.")

    print("\nPaste the ids into .env — exactly as printed, no `models/` prefix.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
