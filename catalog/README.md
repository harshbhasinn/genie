# The Capability Catalog

What Genie can do, in Genie's own vocabulary — not the API's.

The ontology (`../ontology/`) says **what the product means**. This catalog says **what Genie does
about it**. One file per domain.

---

## Why this is authored, not generated

The backend exposes hundreds of endpoints. Perhaps 40–80 of them are things a person actually wants
to *do*. Auto-generating a catalog from controllers produces confident nonsense — the model picks a
plausible-sounding CRUD endpoint and calls it.

So: a capability is a **user-meaningful operation**, written by hand, reviewed like code. It may span
several endpoints, or none at all.

---

## The four modes

Every capability declares a `genie_mode` — what Genie is allowed to do with it.

| mode | Genie | Example |
|---|---|---|
| `answer` | Reads and explains. Changes nothing | *"What's my wallet balance?"* |
| `navigate` | Deep-links to the right screen | *"Take me to inventory"* |
| `plan` | Runs the deterministic planner | *"Plan a 2.5 lakh campaign"* |
| `prefill` | Seeds a form and stops. **A human submits** | *"Set that up for Coca-Cola"* |
| `handoff` | Explains, then deep-links. Genie **cannot** complete it | *"Top up my wallet"* — OTP-gated |

**`handoff` is not a failure mode.** Nine open-service domains are OTP-gated — campaign, wallet,
batch, contract, deployment plan, promo banner, client admin, member, client. Genie can explain
exactly what will happen and take you to the screen. It can never finish the job, and it says so.

---

## Follow-ups — asking instead of guessing

**A question with more than one correct answer is not a blocked question. It is a question that
needs one more turn.**

This is the difference between Genie being unhelpful and Genie being careful. *"What's my wallet
balance?"* has three valid answers, so Genie asks which — and lists them, with a hint on each. It
does not pick one, and it does not refuse.

### The four kinds

| kind | When | Example |
|---|---|---|
| `disambiguation` | Several well-defined things share a name | *"Which balance — main wallet, team sub-wallets, or total credits?"* |
| `entity_choice` | The name matches several real records | *"You have three Nykaa campaigns — which one?"* |
| `slot_fill` | A required input is missing | *"What budget should I plan against?"* |
| `scope` | The window or filter is unstated and matters | *"For this month, or the campaign's whole flight?"* |

### The rules

1. **Context resolves before asking.** If `ui_context` already answers it — the user is on the Wallet
   screen with a card selected — **do not ask**. Ambiguity that the screen can resolve must never
   reach the user as a question. That is the entire point of screen awareness.
2. **One question per turn.** Never a six-field interrogation. `max_asks` caps it.
3. **Every option carries a hint.** *"Main wallet — available to allocate"* is answerable. *"Main
   wallet"* alone makes the user guess what the difference is.
4. **Options are real.** Each points at an ontology metric or entity that Genie can actually serve.
   Never offer a choice that leads to "I don't know".
5. **A follow-up is not a confirmation.** Confirmations guard writes; follow-ups clarify reads. They
   are different mechanisms with different state.
6. **If the user does not answer, take `default_after_asks` or drop it.** Never re-ask the same
   question twice in a session.

### What this unlocks

Several recorded conflicts are **naming disagreements between screens**, not gaps in what Genie
knows. Genie can answer those today by asking which one the user means — while the *ruling* stays
open for the separate question of what a particular card on a particular screen shows.

| Conflict | Still blocks | Does **not** block |
|---|---|---|
| R4 wallet balance | Explaining the Command Centre's card · the runway advisor | Answering *"what's my balance?"* — ask which |
| R8 screen terms | Explaining a specific screen's tile | Answering *"how many screens?"* — ask which |
| R12 reach · R16 verified · R18 impressions | The same | The same |

So a blocked metric is not always a silent Genie. **Blocked means "I cannot tell you what THIS CARD
means". It does not mean "I cannot answer your question".**

---

## Layout

```
catalog/
  README.md                  this file
  schema/capability.schema.json
  wallet.yaml
  campaign.yaml
  inventory.yaml
  brand.yaml
  creative.yaml
  navigate.yaml              pure deep-links, no LLM past classification
```

## Status lifecycle

Same four states as the ontology. A capability is only offered to users at `verified`.

| status | Meaning |
|---|---|
| `verified` | Utterances, gate and behaviour all checked |
| `pending_verification` | Authored; the gate or the execution path is unconfirmed |
| `blocked` | Waiting on a ruling or an API |
| `pending_transcription` | Known to exist, not yet written |

## Execution paths are deliberately empty

The dashboard is moving to a new API surface. Every capability's `execution` block is left blank or
marked pending on purpose — **the identity of a capability (what it is, how people ask for it, who
may do it) is stable; the path is a fill-in.** Authoring the hard part now means the new endpoints
drop into a shape that is already right.

## Commands

```bash
python tools/validate_catalog.py          # schema, cross-references to the ontology, follow-up integrity
python tools/validate_catalog.py --strict # also fail on anything not yet verified
```
