## What changed

<!-- One or two lines. Include any R / D / B / C id this resolves or raises. -->

## Checks

- [ ] All five validators pass locally
- [ ] Generated files **regenerated, not hand-edited** (`render_kb.py`, `verify_sources.py --write`)
- [ ] Any new id is inside my reserved range — see CONTRIBUTING.md §3
- [ ] No credential or `sweep-results.json` in the diff

## If this touches the ontology

- [ ] The `field` genuinely exists on that endpoint — `verify_sources.py` agrees, not just the summary text
- [ ] `scope_key` is right (`owner_id` = who entered your stores · `client_id` = your creatives wherever they played)
- [ ] `verified` carries `verified_against`; `blocked` carries `blocked_by` **and** `blocked_note`
- [ ] Nothing presents a suppressed value as zero

## If this touches a schema

- [ ] Flagged to the other of us before pushing — it revalidates every file underneath it
