#!/usr/bin/env python3
"""Talk to a running Genie and print its answer, block by block.

    python scripts/ask.py "what is my wallet balance"
    python scripts/ask.py "what is my wallet balance" --screen wallet_management
    python scripts/ask.py "pause this campaign" --screen campaign_detail
    python scripts/ask.py --chat                 # interactive, keeps the session

Works the same in PowerShell and bash, which `curl` does not (in PowerShell `curl` is
an alias for Invoke-WebRequest).
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
        body["sessionId"] = session_id
    if screen or role:
        body["uiContext"] = {"screen": screen, "role": role}

    started = time.perf_counter()
    try:
        resp = client.post(f"{base}/v1/genie/chat", json=body)
    except httpx.ConnectError:
        print(f"{RED}Cannot reach {base}{RESET}\n"
              f"  Start it:  cd service && python -m uvicorn genie.main:app --port 9030")
        sys.exit(1)
    if raw:
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
        return resp.json().get("sessionId", session_id)
    if resp.status_code != 200:
        print(f"{RED}HTTP {resp.status_code}{RESET} {resp.text[:400]}")
        return session_id

    answer = resp.json()
    new_session = answer["sessionId"]
    print(f"{DIM}session {new_session[:8]} · responder={answer['mode']}{RESET}")
    for data in answer["blocks"]:
        kind = data["type"]
        if kind == "capability":
            meta = data.get("meta") or {}
            otp = f" {YELLOW}[OTP-gated]{RESET}" if meta.get("otpGated") else ""
            print(f"{GREEN}▸ {data['content']}{RESET} {DIM}({meta.get('mode')}){RESET}{otp}")
        elif kind == "follow_up":
            print(f"{YELLOW}? {data['content']}{RESET}")
            for opt in (data.get("meta") or {}).get("options") or []:
                hint = f"  {DIM}— {opt['hint']}{RESET}" if opt.get("hint") else ""
                print(f"    · {BOLD}{opt['label']}{RESET}{hint}")
        elif kind == "text":
            print(data["content"])
        elif kind == "error":
            print(f"{RED}error{RESET} {data['content']}")
        else:
            print(f"{DIM}[{kind}]{RESET} {data['content'][:160]}")
    total = int((time.perf_counter() - started) * 1000)
    print(f"\n{DIM}{len(answer['blocks'])} blocks · {total}ms{RESET}\n")
    return new_session


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?", help="what to ask")
    ap.add_argument("--screen", help="ui_context.screen, e.g. command_centre")
    ap.add_argument("--role", default="AD_PARTNER_ADMIN")
    ap.add_argument("--base", default="http://127.0.0.1:9030")
    ap.add_argument("--chat", action="store_true", help="interactive, keeps the session")
    ap.add_argument("--raw", action="store_true", help="print the JSON answer verbatim")
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
