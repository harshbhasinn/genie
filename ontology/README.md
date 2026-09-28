# The Dashboard Ontology

What the dashboard is, written as data. One source of truth for **two** consumers:

1. **The (i) tooltips** in the dashboard UI
2. **Genie** — metric explanations, screen context, deep links, capability gates

They read the same files, so they cannot drift.

---

## Layout

```
ontology/
  README.md              this file
  schema/
    screen.schema.json   JSON Schema — every screen file validates against it
  screens/
    command-centre.yaml
    …one file per screen
  enums.yaml             shared vocab: roles, units, freshness classes, scope keys
```

## The status lifecycle

Every metric carries a `status`. **Only `verified` metrics are served to users.**

| status | Meaning | Genie behaviour |
|---|---|---|
| `verified` | Definition checked against the code that computes it | Serves it |
| `pending_verification` | Transcribed from the design, not yet checked against code | **Does not serve.** "I'm not confident in that definition yet" |
| `blocked` | A ruling is outstanding (see `blocked_by`) | **Does not serve.** Names the conflict |
| `pending_transcription` | Known to exist on the screen; definition not yet captured | Not served, not counted as coverage |

This is the mechanism that stops `FINDINGS.md` §6's contradictions reaching a customer. A metric
with two meanings is `blocked`, not guessed.

**S1's exit gate is: zero `pending_verification` on Command Centre and Launch wizard.** `blocked` is
allowed at exit — it means we are waiting on a human, not that we skipped the work.

## Required fields on a metric

| Field | Why |
|---|---|
| `id`, `label` | Identity, and what the UI shows |
| `definition` | Plain business language. **This is the tooltip text.** |
| `formula` | How it is computed, in the deck's own notation where available |
| `unit` | `percent` · `currency_inr` · `count` · `seconds` · `ratio` · `cpm_inr` |
| `source.api` | The BFF endpoint that produces it |
| `source.scope_key` | **`owner_id` or `client_id`** — see below. Wrong key = another party's data |
| `freshness` | `live` · `batch_6h` — drives the `as_of` stamp Genie must show |
| `status` | Above |
| `verified_against` | The class/RPC/aggregation checked. Required when `status: verified` |

Optional but high-value: `gotchas` (what users actually ask about), `good_range`, `owner`.

## Two rules that are not style preferences

**1. `scope_key` is mandatory and load-bearing.**
`dashboard_views` scopes by `owner_id` (everyone who entered *your stores*); `content_views` scopes
by `client_id` (only *your creatives*, wherever they played). On seed data Zudio had 210 content
docs of its own while 462 played on its screens — 252 correctly invisible to it. **Resolve the wrong
key and Genie reports another tenant's data.**

**2. `definition` is verified against code, never against the design.**
The design says what someone intended; the aggregation is what exists. `FINDINGS.md` §6 is eight
cases where those disagreed. A definition that cannot be verified is a **failing test**, not a
warning.

## Commands

```bash
python tools/validate_ontology.py            # schema + cross-references + coverage report
python tools/validate_ontology.py --strict   # also fail on pending_verification  (CI gate for S1 exit)
```

Planned for later in S1: `verify` (definition vs code, assisted), `render` (→ `kb/generated/`),
`diff` (vs the deployed version).

## Provenance

Transcribed from `Agency Admin Dashboard (1.5).docx`, the red annotation cards. Where the deck gives
a worked number it is kept in `formula` as written — it is the design's own arithmetic and is useful
for cross-checking against real data, but it is **not** a value to serve.
