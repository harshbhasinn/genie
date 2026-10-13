#!/usr/bin/env python3
"""Push kb/generated/ into Genie's knowledge base on the chotu_rag service.

chotu_rag is the write side of retrieval: it chunks, embeds and stores, and its
admin UI is where hand-written documents (Tier A policy, SOPs) get added. This
keeps the generated half of that knowledge base in step with the ontology, so
the ontology stays the source of truth and nobody re-uploads screens by hand.

    python tools/sync_kb.py            show what would change, touch nothing
    python tools/sync_kb.py --apply    create the knowledge base if missing, then sync

Reads ADMIN_API_KEY from the environment (it lives in chotu_rag/.env) and the
service address from CHOTU_BASE, default http://127.0.0.1:8100.

Only documents this script uploaded are ever replaced or removed. They carry
`source_kind: generated` in their metadata, so anything added through the admin
UI is left alone even when it sits in the same knowledge base.

Stdlib only, apart from pyyaml.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("needs deps:  pip install -r tools/requirements.txt")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "kb" / "generated"

DEFAULT_BASE = "http://127.0.0.1:8100"
KB_SLUG = "genie"
KB_NAME = "Genie"
KB_DESCRIPTION = ("Genie (AdGuide) for the Agency Admin Dashboard. Screen docs are "
                  "generated from the ontology by tools/sync_kb.py; add policy and "
                  "SOP documents here by hand.")
# Fixed at creation and never changeable afterwards. Matches RAG.md section 7.
EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_DIMENSIONS = 3072

FOLDER = "/screens/"
# Not one of chotu_rag's prose types (concept, guide, policy, ...). Those are held
# to its merchant authoring contract - a Hinglish section, Devanagari phrasings -
# which a screen reference for agency admins cannot meet and should not try to.
# Any other type still gets the heading-aware prose chunker.
DOC_TYPE = "screen"

# The banner tells a human editing the file where its source is. Embedded, it
# is the same two lines of noise at the top of every document's first chunk.
BANNER_PREFIXES = ("> **Generated from the ontology", "> Change `ontology/")


class Client:
    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token

    def request(self, method, path, body=None, kb=None, form=None):
        headers = {"Authorization": f"Bearer {self.token}"}
        if kb:
            headers["X-Knowledge-Base"] = kb
        data = None
        if form is not None:
            boundary = uuid.uuid4().hex
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            data = _multipart(form, boundary)
        elif body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method=method)
        try:
            # Generous: an upload waits on the embedding provider, which paces itself.
            with urllib.request.urlopen(req, timeout=300) as resp:
                raw = resp.read().decode()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"detail": raw[:400]}
        except urllib.error.URLError as e:
            sys.exit(f"Cannot reach chotu_rag at {self.base}: {e.reason}\n"
                     f"Start it with:  uvicorn app.main:app --port 8100  (in chotu_rag/)")


def _multipart(parts, boundary: str) -> bytes:
    buf = io.BytesIO()
    for part in parts:
        buf.write(f"--{boundary}\r\n".encode())
        if len(part) == 2:
            name, value = part
            buf.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            buf.write(str(value).encode())
        else:
            name, filename, content = part
            buf.write(f'Content-Disposition: form-data; name="{name}"; '
                      f'filename="{filename}"\r\nContent-Type: text/markdown\r\n\r\n'.encode())
            buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    return buf.getvalue()


def read_local() -> dict[str, dict]:
    """Every generated doc, keyed by doc_id, with the bytes to upload."""
    docs = {}
    for p in sorted(GENERATED.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        _, front, body = text.split("---\n", 2)
        meta = yaml.safe_load(front) or {}
        body = "\n".join(line for line in body.split("\n")
                         if not line.startswith(BANNER_PREFIXES))
        docs[meta["doc_id"]] = {
            "path": p,
            "title": meta["title"],
            "digest": meta.get("source_digest"),
            "payload": f"---\n{front}---\n{body}".encode("utf-8"),
        }
    return docs


def read_remote(client: Client, kb: str) -> dict[str, list[dict]]:
    """Documents this script owns in the knowledge base, keyed by doc_id."""
    out: dict[str, list[dict]] = {}
    skip = 0
    while True:
        status, body = client.request("GET", f"/api/v1/documents?skip={skip}&limit=100",
                                      kb=kb)
        if status != 200:
            sys.exit(f"Could not list documents in '{kb}': {status} {body.get('detail')}")
        for d in body["documents"]:
            meta = d.get("metadata") or {}
            if meta.get("source_kind") == "generated" and meta.get("doc_id"):
                out.setdefault(meta["doc_id"], []).append(d)
        skip += 100
        if skip >= body["total"]:
            return out


def ensure_kb(client: Client, kb: str, apply: bool) -> bool:
    """True if the knowledge base exists (or was just created)."""
    status, body = client.request("GET", f"/api/v1/knowledge-bases/{kb}")
    if status == 200:
        print(f"knowledge base '{kb}' exists - {body['embedding_model']}, "
              f"{body['embedding_dimensions']}d")
        return True
    if status != 404:
        sys.exit(f"Could not look up '{kb}': {status} {body.get('detail')}")
    if not apply:
        print(f"knowledge base '{kb}' does not exist - would create it "
              f"({EMBEDDING_MODEL}, {EMBEDDING_DIMENSIONS}d, in the service's own database)")
        return False

    # Ask the service which provider serves the model rather than hardcoding one.
    # chotu_rag rejects a stale model/provider pairing outright, and a hardcoded
    # "gemini" is exactly how its own loader once stopped being able to create one.
    status, body = client.request("GET", "/api/v1/embedding-models")
    spec = next((m for m in body.get("models", []) if m["model"] == EMBEDDING_MODEL), None)
    if status != 200 or not spec:
        sys.exit(f"The service does not offer {EMBEDDING_MODEL}: {status} {body}")
    if not spec.get("key_configured"):
        sys.exit(f"The service has no API key for provider '{spec['provider']}'.")

    status, body = client.request("POST", "/api/v1/knowledge-bases", body={
        "name": KB_NAME,
        "slug": kb,
        "description": KB_DESCRIPTION,
        "embedding_provider": spec["provider"],
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
    })
    if status != 201:
        sys.exit(f"Could not create '{kb}': {status} {body.get('detail')}")
    print(f"created knowledge base '{kb}' - {body.get('dsn_preview', 'service database')}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    ap.add_argument("--base", default=os.environ.get("CHOTU_BASE", DEFAULT_BASE))
    ap.add_argument("--kb", default=KB_SLUG, help=f"knowledge base slug (default {KB_SLUG})")
    args = ap.parse_args()

    token = os.environ.get("ADMIN_API_KEY")
    if not token:
        sys.exit("ADMIN_API_KEY is not set. Export it from chotu_rag/.env.")
    client = Client(args.base, token)

    status, who = client.request("GET", "/api/v1/auth/me")
    if status != 200:
        sys.exit(f"Could not authenticate at {args.base}: {status} {who.get('detail')}")
    print(f"chotu_rag at {args.base}")

    exists = ensure_kb(client, args.kb, args.apply)
    local = read_local()
    remote = read_remote(client, args.kb) if exists else {}

    add = [d for d in local if d not in remote]
    replace = [d for d in local if d in remote
               and (len(remote[d]) > 1
                    or (remote[d][0].get("metadata") or {}).get("source_digest")
                    != local[d]["digest"])]
    unchanged = [d for d in local if d in remote and d not in replace]
    remove = [d for d in remote if d not in local]

    for label, ids in (("add", add), ("replace", replace), ("remove", remove)):
        for d in ids:
            print(f"  {label:<8} {d}")
    print(f"\n{len(add)} to add · {len(replace)} to replace · {len(remove)} to remove · "
          f"{len(unchanged)} unchanged")

    if not args.apply:
        if add or replace or remove:
            print("Dry run. Re-run with --apply to make these changes.")
        return 0

    failed = 0
    for d in remove + replace:
        for doc in remote[d]:
            status, body = client.request("DELETE", f"/api/v1/documents/{doc['id']}", kb=args.kb)
            if status not in (200, 204):
                failed += 1
                print(f"  FAIL delete {d}: {status} {body.get('detail')}")

    for d in add + replace:
        doc = local[d]
        status, body = client.request("POST", "/api/v1/documents/upload", kb=args.kb, form=[
            ("folder_path", FOLDER),
            ("doc_type", DOC_TYPE),
            ("file", doc["path"].name, doc["payload"]),
        ])
        if status == 201:
            print(f"  ok   {d}  ({body['chunk_count']} chunks)")
        else:
            failed += 1
            print(f"  FAIL {d}: {status} {str(body.get('detail'))[:400]}")

    status, stats = client.request("GET", "/api/v1/stats", kb=args.kb)
    if status == 200:
        print(f"\n'{args.kb}' now holds {stats.get('total_documents')} document(s), "
              f"{stats.get('total_chunks')} chunk(s).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
