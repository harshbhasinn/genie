#!/usr/bin/env python3
"""Render the ontology into knowledge-base prose.

  python tools/render_kb.py            write kb/generated/
  python tools/render_kb.py --check    fail if the committed output is stale (CI gate)

This is a BUILD ARTIFACT. Never hand-edit kb/generated/ — edit the ontology and re-render.
Only `verified` and `pending_verification` entries are rendered; blocked ones are listed
separately so the corpus never asserts a disputed definition as fact.
"""
from __future__ import annotations

import argparse
import sys
import hashlib
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
SCREENS = ROOT / "ontology" / "screens"
OUT = ROOT / "kb" / "generated"

FRONTMATTER = """---
doc_id: {doc_id}
title: {title}
source_kind: generated
audience: [{audience}]
client_scope: null
status: published
owner: genie-build
source_digest: {digest}
verified_against: ontology/screens/{src}
effective_from: null
effective_to: null
---
"""

BANNER = (
    "> **Generated from the ontology. Do not edit.**\n"
    "> Change `ontology/screens/{src}` and re-run `python tools/render_kb.py`.\n"
)


def render_screen(doc: dict, src: str, digest: str = "") -> tuple[str, str]:
    s = doc["screen"]
    sid = s["id"]
    lines: list[str] = []
    lines.append(FRONTMATTER.format(
        doc_id=f"screen-{sid.replace('_', '-')}",
        title=s["title"], digest=digest, src=src,
        # audience is a PRE-ANN retrieval filter, not decoration. It must be the
        # screen's real audience, or a role retrieves docs it may not see.
        audience=", ".join(s["audience"])))
    lines.append(BANNER.format(src=src))
    lines.append(f"\n# {s['title']}\n")
    lines.append(f"{' '.join(s['purpose'].split())}\n")
    lines.append(f"**Where:** `{s['route']}`  ")
    lines.append(f"**Who can see it:** {', '.join(s['audience'])}\n")

    if s.get("nav_label") and s["nav_label"] != s["title"]:
        lines.append(
            f"> The navigation calls this **{s['nav_label']}** while the page heading reads "
            f"**{s['title']}**. Both names refer to the same screen.\n")

    served, disputed = [], []
    for m in s.get("metrics") or []:
        (disputed if m.get("status") == "blocked" else served).append(m)

    if served:
        lines.append("\n## What this screen shows\n")
        for m in served:
            lines.append(f"### {m['label']}\n")
            if m.get("definition"):
                lines.append(f"{' '.join(m['definition'].split())}\n")
            if m.get("formula"):
                lines.append(f"**How it is worked out:** `{m['formula']}`\n")
            bits = []
            if m.get("unit"):
                bits.append(f"unit: {m['unit']}")
            if m.get("freshness") == "batch_6h":
                bits.append("refreshes about every 6 hours, and can change retroactively")
            elif m.get("freshness") == "live":
                bits.append("live")
            if bits:
                lines.append(f"*{' · '.join(bits)}*\n")
            for g in m.get("gotchas") or []:
                lines.append(f"- {' '.join(g.split())}\n")

    if s.get("steps"):
        lines.append("\n## Steps\n")
        for st in sorted(s["steps"], key=lambda x: (x["index"], x.get("applies_to", ""))):
            applies = st.get("applies_to", "both")
            suffix = "" if applies == "both" else f" *(for {applies} campaigns only)*"
            lines.append(f"### {st['index']}. {st['title']}{suffix}\n")
            if st.get("purpose"):
                lines.append(f"{' '.join(st['purpose'].split())}\n")
            fields = [f for f in (st.get("fields") or []) if f.get("status") != "blocked"]
            if fields:
                lines.append("| Field | Required | Notes |")
                lines.append("|---|---|---|")
                for f in fields:
                    req = "yes" if f.get("required") else ""
                    note = f.get("constraints") or (f.get("options")
                                                    and ", ".join(f["options"])) or ""
                    lines.append(f"| {f['label']} | {req} | {' '.join(str(note).split())} |")
                lines.append("")

    acts = [a for a in (s.get("actions") or []) if a.get("status") != "blocked"]
    if acts:
        lines.append("\n## What you can do here\n")
        for a in acts:
            note = ""
            if a.get("otp_gated"):
                note = " — needs an OTP sent to your registered mobile"
            lines.append(f"- **{a['label']}**{note}")
        lines.append("")

    if disputed:
        lines.append("\n## Not yet settled\n")
        lines.append(
            "These appear on the screen but their definition is disputed between screens, so "
            "they are deliberately not described here.\n")
        for m in disputed:
            why = " ".join((m.get("blocked_note") or "").split())
            lines.append(f"- **{m['label']}** — {why}")
        lines.append("")

    return f"screen-{sid.replace('_', '-')}.md", "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if committed output is stale")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    rendered: dict[str, str] = {}
    for p in sorted(SCREENS.glob("*.yaml")):
        raw = p.read_text(encoding="utf-8")
        doc = yaml.safe_load(raw) or {}
        if "screen" not in doc:
            continue
        # Digest of the SOURCE, not today's date. A render date churns the file on
        # every re-render and fails --check on any day after the last one, which
        # trains people to ignore the gate. It also falsely implies someone
        # verified the content, when all that happened was a render.
        digest = hashlib.sha256(raw.replace("\r\n", "\n").encode()).hexdigest()[:12]
        name, body = render_screen(doc, p.name, digest)
        rendered[name] = body

    if args.check:
        stale = []
        for name, body in rendered.items():
            f = OUT / name
            if not f.exists() or f.read_text(encoding="utf-8") != body:
                stale.append(name)
        for f in OUT.glob("*.md"):
            if f.name not in rendered:
                stale.append(f"{f.name} (orphan)")
        if stale:
            print("STALE — re-run `python tools/render_kb.py`:")
            for s in stale:
                print(f"  · {s}")
            return 1
        print(f"kb/generated/ is current ({len(rendered)} file(s)).")
        return 0

    for f in OUT.glob("*.md"):
        if f.name not in rendered:
            f.unlink()
    for name, body in rendered.items():
        # newline="\n" explicitly: text mode would emit CRLF on Windows, and a
        # generated file that differs by platform makes --check disagree across machines.
        (OUT / name).write_text(body, encoding="utf-8", newline="\n")

    words = sum(len(b.split()) for b in rendered.values())
    print(f"Rendered {len(rendered)} file(s) into kb/generated/ — about {words:,} words.")
    print("These are build artifacts. Edit the ontology, not these.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
