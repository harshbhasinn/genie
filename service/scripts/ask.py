#!/usr/bin/env python3
"""Talk to a running Genie. Shows the stream arriving, not a buffered blob.

    python scripts/ask.py "what is my wallet balance"
    python scripts/ask.py "what is my wallet balance" --screen wallet_management
    python scripts/ask.py "pause this campaign" --screen campaign_detail
    python scripts/ask.py --chat                 # interactive, keeps the session

Works the same in PowerShell and bash, which `curl` does not: in PowerShell
`curl` is an alias for Invoke-WebRequest, which buffers the whole response and
hides the one thing worth watching.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIM, BOLD, GREEN, YELLOW, RED, RESET = (
    "\033[2m", "\033[1m", "\033[32m", "\033[33m", "\033[31m", "\033[0m")


def turn(client: httpx.Client, base: str, message: str, *, screen: str | None,
         role: str, session_id: str | None, raw: bool) -> str | None:
    body = {"message": message}
    if session_id:
        body["session_id"] = session_id
    if screen or role:
        body["ui_context"] = {"screen": screen, "role": role}

    started = time.perf_counter()
    first_token_at: float | None = None
    event = None
    new_session = session_id

    try:
        with client.stream("POST", f"{base}/v1/genie/chat", json=body) as resp:
            if resp.status_code != 200:
                raw_body = resp.read().decode(errors="replace")
                print(f"{RED}HTTP {resp.status_code}{RESET} {raw_body[:400]}")
                return session_id
            for line in resp.iter_lines():
                if raw:
                    print(line)
                    continue
                if line.startswith("event: "):
                    event = line[7:].strip()
                    continue
                if not line.startswith("data: "):
                    continue
                data = json.loads(line[6:])

                if event == "start":
                    new_session = data["session_id"]
                    print(f"{DIM}session {new_session[:8]} · responder={data['mode']}{RESET}")
                elif event == "block":
                    kind = data["type"]
                    if kind == "token":
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        print(data["content"], end="", flush=True)
                    elif kind == "capability":
                        meta = data.get("meta") or {}
                        otp = f" {YELLOW}[OTP-gated]{RESET}" if meta.get("otp_gated") else ""
                        print(f"{GREEN}▸ {data['content']}{RESET} "
                              f"{DIM}({meta.get('mode')}){RESET}{otp}")
                        if meta.get("follow_ups"):
                            print(f"{DIM}  will ask: {', '.join(meta['follow_ups'])}{RESET}")
                    elif kind == "error":
                        print(f"\n{RED}error{RESET} {data['content']}")
                    elif kind == "text":
                        pass  # already streamed as tokens
                elif event == "done":
                    total = int((time.perf_counter() - started) * 1000)
                    ttft = (f" · first token {int((first_token_at - started) * 1000)}ms"
                            if first_token_at else "")
                    print(f"\n{DIM}{data['blocks']} blocks · {total}ms{ttft}{RESET}\n")
    except httpx.ConnectError:
        print(f"{RED}Cannot reach {base}{RESET}\n"
              f"  Start it:  cd service && python -m uvicorn genie.main:app --port 9030")
        sys.exit(1)
    return new_session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?", help="what to ask")
    ap.add_argument("--screen", help="ui_context.screen, e.g. command_centre")
    ap.add_argument("--role", default="AD_PARTNER_ADMIN")
    ap.add_argument("--base", default="http://127.0.0.1:9030")
    ap.add_argument("--chat", action="store_true", help="interactive, keeps the session")
    ap.add_argument("--raw", action="store_true", help="print the SSE frames verbatim")
    args = ap.parse_args()

    if not args.message and not args.chat:
        ap.error("give a message, or --chat")

    with httpx.Client(timeout=120.0) as client:
        try:
            health = client.get(f"{args.base}/healthz").json()
            onto = health["ontology"]
            print(f"{DIM}{args.base} · responder={health['responder']} · "
                  f"{onto['screens']} screens · {onto['capabilities']} capabilities{RESET}")
            if tok := health.get("token", {}).get("hint"):
                print(f"{YELLOW}token: {tok}{RESET}")
        except httpx.ConnectError:
            print(f"{RED}Cannot reach {args.base}{RESET}\n"
                  f"  Start it:  cd service && "
                  f"python -m uvicorn genie.main:app --port 9030")
            return 1
        print()

        session = None
        if args.message:
            session = turn(client, args.base, args.message, screen=args.screen,
                           role=args.role, session_id=session, raw=args.raw)
        if args.chat:
            print(f"{DIM}interactive — blank line or Ctrl-C to quit{RESET}\n")
            while True:
                try:
                    text = input(f"{BOLD}you ›{RESET} ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not text:
                    break
                session = turn(client, args.base, text, screen=args.screen,
                               role=args.role, session_id=session, raw=args.raw)
    return 0


if __name__ == "__main__":
    sys.exit(main())
