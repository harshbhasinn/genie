# Genie — the specification and the tooling

**Genie** (called **AdGuide** in the designs) is the assistant embedded in the AdGrid **Ad Partner
Agency Admin Dashboard**. It explains every number on the screen, answers questions about live
figures, plans campaigns with real metrics, and hands off anything that writes.

> ### This repository is private, and must stay private.
> It documents internal API surface, permission names, authorization gates, wallet and GST
> mechanics, and rate-card semantics. None of that is public.

---

## What this repo is — and is not

**It is the specification, the knowledge base and the test tooling.** Versioned data describing every
screen, metric, capability and gate, plus the validators that keep that description honest.

**It is not the service.** No FastAPI app lives here yet. When it does (stage S2) it will consume
this repo as its source of truth rather than replace it.

That split is deliberate. The hard part of this product was never the chat loop — it is knowing what
"utilisation" means on which screen, which of four things called "reach" the user is asking about,
and which endpoint can honestly answer. That knowledge is data, it is reviewable, and it outlives any
implementation.

---

## The one invariant

**Genie holds zero write paths.** It reads, proposes and prefills. A human always commits.

This is enforced by schema, not by convention. Every capability declares `state_changing`, which is
deliberately **not** GET-vs-POST — `simulate-cost`, `availability-search`, `inventory/heatmap` and
`dashboard/content` are POSTs that change nothing. The catalog schema rejects any capability where
`state_changing: true` and Genie's method is not `NONE`.

Twelve state-changing endpoints are recorded with `method: NONE`, so Genie knows exactly what each
one *is* and can explain it precisely while being structurally unable to call it.

`python tools/validate_catalog.py` fails the build if that is ever violated.

---

## Layout

```
ontology/          12 screens · 94 metrics · 20 form fields · 5 approval transitions
  schema/          JSON Schema the screens are validated against
  screens/         one YAML per dashboard screen — the backbone
catalog/           49 capabilities across 7 domains, with 25 clarifying follow-ups
  schema/          JSON Schema, including the read-only invariant
tests/
  golden-queries.yaml   106 queries with the right answer written down in advance
kb/
  generated/       12 docs rendered FROM the ontology — build output, do not edit
  inbox/           where authored policy documents will land (empty; see D3)
reference/
  bff-openapi.json the live QA BFF spec — 236 paths, 261 operations, 620 schemas
tools/             7 scripts, all stdlib + pyyaml + jsonschema
asks/              what we need from other teams, ready to send
```

### Documents, in the order worth reading them

| | |
|---|---|
| **[STATUS.md](STATUS.md)** | **Start here.** Where all twelve stages actually stand, measured from the repo |
| [RESOLUTION-POLICY.md](RESOLUTION-POLICY.md) | **Read second.** The precedence rule you will need on day one |
| [BUILD.md](BUILD.md) | The twelve stages, their dependencies, and how to test before touching QA |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How Genie embeds, the two engines, the read and write paths |
| [CONFLICTS.md](CONFLICTS.md) | Every contradiction found in the designs — 33 ids, what each resolved to |
| [VERIFY.md](VERIFY.md) | Generated. What still needs a live token to confirm |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How two people work in here without fighting over YAML |
| [RAG.md](RAG.md) · [KNOWLEDGE.md](KNOWLEDGE.md) | Retrieval design and the three-store model |
| [SERVICES.md](SERVICES.md) | BFF → open-service → domain map, and the OTP pattern |

`PLAN.md` is the original build plan. Its §8 and §16 are superseded by `BUILD.md` and
`ARCHITECTURE.md` — kept for history, not for reference.

---

## Quickstart

```bash
git clone <repo-url> && cd Genie
python -m venv .venv && . .venv/Scripts/activate     # Windows
#                        source .venv/bin/activate     # macOS / Linux
pip install -r tools/requirements.txt

python tools/validate_ontology.py       # schema + cross-references + coverage
python tools/validate_catalog.py        # capabilities, follow-ups, read-only invariant
python tools/validate_golden.py         # every id in the query set still resolves
python tools/verify_sources.py --check  # every declared field exists on its endpoint
python tools/render_kb.py --check       # kb/generated/ is not stale
```

All five must pass. CI runs exactly these on every push and pull request.

### The tools

| | What it does |
|---|---|
| `validate_ontology.py` | Screens against JSON Schema, cross-references, and the status ledger |
| `validate_catalog.py` | Capabilities, utterance collisions, follow-up sanity, and **the read-only invariant** |
| `validate_golden.py` | Every capability, metric, screen, role and follow-up id in the query set exists — so a rename breaks the test set loudly instead of silently |
| `verify_sources.py` | **Statically** checks each metric's declared field against the OpenAPI response schema. Found 11 metrics pointing at an endpoint that returns four fields. `--write` regenerates `VERIFY.md` |
| `render_kb.py` | Renders `kb/generated/` from the ontology. `--check` is the staleness gate |
| `sweep.py` | **Needs a live token.** Calls every mapped endpoint and reports what actually came back, including a cross-tenant negative pass. Stdlib only, so it runs on a locked-down QA box |
| `inspect_openapi.py` | Ad-hoc exploration of the vendored spec |

---

## The rule that will save you an afternoon

From `RESOLUTION-POLICY.md`, adopted because the designs contain a lot of mistakes and stopping for
each one was costing more than it saved:

> **When the designs and the running system disagree, the running system is the answer.** Record the
> divergence as a design defect, adopt the backend's meaning, and keep moving.

Precedence: **the live API** → **backend source and contracts** → **product owner rulings** → **the
Figma deck**, lowest.

Two consequences worth internalising before you touch anything:

- `status: blocked` means **no source exists anywhere**. It does *not* mean two screens disagree.
  That is why the blocked count has gone *up* as the work has got more honest — the real gaps became
  visible once naming arguments stopped occupying the same field.
- **Suppression is not zero.** Below the k-anonymity floor of 10 the API returns `suppressed: true`
  with a `reason`. Rendering that as `0` invents an absence of people, and it is the single most
  dangerous confusion in this product.

---

## Where it stands

Two of twelve stages are complete. Ten are not, and **none of the ten is waiting on modelling work** —
every one waits on an access grant, a provider decision, or a Postgres instance. See
[STATUS.md](STATUS.md) for the detail and [asks/](asks/) for what has to be requested.

```
S0  Unblock                 ████████████████████░  95%
S1  Ontology + catalog      █████████████████████ 100%
S2  Service skeleton        ░░░░░░░░░░░░░░░░░░░░░   0%   ← next, and cheap
S3  Navigate + explain      ░░░░░░░░░░░░░░░░░░░░░   0%
S4  Data path               ░░░░░░░░░░░░░░░░░░░░░   0%
S5  Reach model             ░░░░░░░░░░░░░░░░░░░░░   0%   ← longest pole
S6  Planner                 ░░░░░░░░░░░░░░░░░░░░░   0%
S7  Calculator tab          ░░░░░░░░░░░░░░░░░░░░░   0%
S8  Prefill bridge          ░░░░░░░░░░░░░░░░░░░░░   0%
S9  Advisors                ░░░░░░░░░░░░░░░░░░░░░   0%
S10 Retrieval + KB          ████░░░░░░░░░░░░░░░░░  20%
S11 Hardening               ░░░░░░░░░░░░░░░░░░░░░   0%
```

---

## The design deck is not in this repo

`Agency Admin Dashboard (1.5).docx` is 75 MB of embedded images and is deliberately gitignored.
Figma is the canonical source, everything in the deck is already transcribed into `ontology/`, and
the resolution policy ranks it below the live API anyway — so it would be the highest storage cost
for the lowest-precedence source. Ask for the Figma link instead.
