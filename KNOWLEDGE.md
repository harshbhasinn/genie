# Knowledge — authoring, ingestion, operations

How content gets into Genie: the three stores, the authoring format, the ingest CLI, and what to do
with the `.md` files you already have.

Design rationale lives in `RAG.md`. This document is the **operations**. Date: 2026-09-18.

---

## 1. Three stores, not one

The most common mistake is one "knowledge base" holding everything. Genie has **three** content
stores with different formats, loaders and version stamps.

| | Store | Format | Location | Loaded | Version stamp |
|---|---|---|---|---|---|
| **1** | **Ontology** | YAML, structured | `ontology/` | At startup, validated | `ontology_version` |
| **2** | **Capability catalog** | YAML, structured | `catalog/` | At startup, validated | `catalog_version` |
| **3** | **Knowledge base** | Markdown + frontmatter | `kb/` | `genie-kb ingest` → pgvector | `kb_version` |

Only **store 3** is RAG. Stores 1 and 2 are exact lookups (`RAG.md` §1, the ladder) and must never
be embedded — that is how "budget utilization" comes back as "batch utilization".

**All three live in git and are reviewed as pull requests.** Not a CMS, not an upload button. A
policy change is a PR, which is what makes `owner` and `verified_at` mean anything, makes rollback
`git revert`, and makes the ingest reproducible.

---

## 2. Where your `.md` files go — triage first

**Do not bulk-ingest them.** `FINDINGS.md` §2.2 has three worked examples of good engineering
documents that would make Genie quote a renamed enum, a mocked value, and an unratified proposal to
a customer.

Put them in `kb/inbox/` and run each through this:

```
                   ┌──────────────────────────────┐
                   │  a document you have         │
                   └──────────────┬───────────────┘
                                  ▼
          ┌───────────────────────────────────────────┐
          │ Does it define a METRIC, SCREEN, WIDGET   │
          │ or ACTION?                                │──▶ YES ──▶  ontology/  (store 1)
          └───────────────────────┬───────────────────┘             structured, never embedded
                                  │ NO
                                  ▼
          ┌───────────────────────────────────────────┐
          │ Does it describe something Genie can DO?  │──▶ YES ──▶  catalog/   (store 2)
          └───────────────────────┬───────────────────┘
                                  │ NO
                                  ▼
          ┌───────────────────────────────────────────┐
          │ Is it internal engineering — architecture,│
          │ ports, handoffs, build plans, conversation│──▶ YES ──▶  EXCLUDE
          │ logs, anything an agency must never see?  │             (or audience: [ADOPS_ADMIN])
          └───────────────────────┬───────────────────┘
                                  │ NO
                                  ▼
          ┌───────────────────────────────────────────┐
          │ Does it contain live numbers about a      │──▶ YES ──▶  EXCLUDE the numbers.
          │ specific client, or entity names as data? │             Label survivors "worked example"
          └───────────────────────┬───────────────────┘
                                  │ NO
                                  ▼
          ┌───────────────────────────────────────────┐
          │ Is it ratified, or a draft / proposal?    │──▶ DRAFT ─▶ status: draft (not retrievable)
          └───────────────────────┬───────────────────┘
                                  │ RATIFIED
                                  ▼
                              kb/  (store 3)  ──  add frontmatter, §4
```

**The rule that catches most of it: default is deny.** A document enters the corpus because someone
tagged it `audience:` and `status: published`, not because it was in the folder.

Drop the files in `kb/inbox/` and I'll run the triage and report which go where and why.

---

## 3. Corpus layout

```
kb/
  inbox/                    ← staging. Nothing here is ingested. Triage empties it.
  policy/                   Tier A — the gap that must be written
    billing-settlement.md
    rate-cards.md
    batch-lifecycle.md
    campaign-policy.md
    cancellation-refunds.md
  sop/                      Tier B — ad ops runbooks
    campaign-underdelivery.md
    device-offline.md
  faq/                      Tier E — mined from real tickets (highest value per word)
    wallet-questions.md
  release-notes/            Tier D — "what changed in the dashboard?"
    2026-09.md
  generated/                Tier C — DO NOT EDIT. Rendered from ontology/ by the CLI.
    metrics.md
    screens.md
```

`generated/` is a build artifact. It is regenerated on every ontology change so the prose and the
structured definitions cannot disagree, and it is committed only so diffs are reviewable.

**Tier A is the real gap** — roughly 15–25 documents that do not exist yet. It is a business-writing
task with an engineering deadline, and it is on the critical path for Stage 10. Nothing about it
depends on code, so it can start today.

---

## 4. Authoring format

Every KB document is markdown with YAML frontmatter. **The frontmatter is the metadata contract from
`RAG.md` §6, and it is enforced, not advisory.**

```markdown
---
doc_id: billing-settlement
title: Revenue-share settlement
source_kind: policy                  # policy | sop | faq | release_note | generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER]
client_scope: null                   # null = global; else a client id
status: published                    # published | draft | deprecated
owner: finance@adgrid.ai
verified_at: 2026-09-18
verified_against: BillingGrpcService.RunMonthSettlement
effective_from: 2026-04-01
effective_to: null
---

# Revenue-share settlement

## When settlement runs

Settlement for revenue-share batches runs on the 7th of the following month…

## What is included

| Component | Included | Note |
|---|---|---|
| Rev-share consumption | Yes | … |
| Screen rental | Yes | … |
```

### The four fields that are correctness, not metadata

| Field | If you get it wrong |
|---|---|
| `audience` | An agency admin retrieves an internal ADOPS runbook |
| `client_scope` | A cross-tenant leak — one client's contract terms quoted to another |
| `status` | A draft policy quoted as policy (`FINDINGS.md` §2.2, third trap) |
| `effective_from/to` | Last quarter's invoice answered from this quarter's rate card |

These are **pre-ANN filters**. Filtering after retrieval is not equivalent: top-K silently shrinks to
nothing and Genie says "I don't know" about things it does know.

### Authoring rules

- **One document, one topic.** Splitting is free; merging later is a re-embed.
- **Headings are load-bearing** — they become the contextual header prepended to every chunk.
- **Tables stay whole.** Never split one across a heading boundary.
- **No live numbers.** If a figure is needed, it is a worked example and must be written as one.
- **No entity names as content.** Campaign, brand and store names change daily; entity resolution is
  an API call, always.

---

## 5. The ingest CLI

```bash
genie-kb validate            # frontmatter, schema, secrets, chunk-length distribution
genie-kb plan                # diff vs the database: what would be added / updated / removed
genie-kb ingest [--dry-run]  # embed changed chunks, upsert, soft-delete absent, bump kb_version
genie-kb status              # current kb_version, doc and chunk counts, last ingest
genie-kb search "how does settlement work"   # retrieval smoke test from the terminal
genie-kb eval                # recall@5 + isolation checks against the golden set
```

### Pipeline

```
 kb/**/*.md  (git)
      │
      ▼
 1  DISCOVER          walk kb/, skip inbox/
      ▼
 2  VALIDATE          frontmatter → ChunkMeta
                      ✗ missing audience or status  → FAIL THE BUILD
      ▼
 3  SCAN              secrets, connection strings, PII
                      ✗ any hit                     → FAIL THE BUILD
      ▼
 4  CHUNK             heading-aware, 800–1200 chars, 150 overlap
                      tables whole · policy clauses one per chunk
      ▼
 5  CONTEXTUALISE     prepend  [Doc Title > Section > Subsection]
      ▼
 6  HASH              content hash per chunk → skip unchanged
      ▼
 7  EMBED             batched, only changed chunks
                      QUERY_PREFIX / DOCUMENT_FORMAT from the frozen constant (RAG.md §7)
      ▼
 8  UPSERT            by chunk_id; soft-delete chunks no longer present
      ▼
 9  VERSION           bump kb_version → invalidates L2/L3 cache atomically
      ▼
10  EVAL              recall@5 + audience/client-scope isolation
                      ✗ regression                  → FAIL THE BUILD
```

**Steps 2, 3 and 10 fail the build.** That is the whole point — a KB that can be broken by a careless
merge is a KB that will be.

### Where it runs

| | When | What |
|---|---|---|
| Local | Before committing | `genie-kb validate && genie-kb plan` |
| CI, on PR | Every KB change | `validate` + `plan`, posts the diff as a PR comment |
| CI, on merge to main | Automatic | `ingest` against QA, then `eval` |
| Production | Manual promote | Same artifact, after QA eval passes |

**Never ingest from a developer laptop into production.** The CLI should refuse a production DSN
without an explicit `--i-know-what-im-doing` flag, because the failure mode — a half-ingested corpus
with a bumped `kb_version` — invalidates every cache and serves nothing.

### Re-embedding

Only changed chunks are embedded, keyed by content hash. A full re-embed is triggered by changing
`QUERY_PREFIX`, `DOCUMENT_FORMAT`, the model, or the dimensions — and the CLI should **refuse** those
changes without `--full-reembed`, because silently mixing vectors from two models degrades retrieval
with no error.

Note from `RAG.md` §7: the document side embeds `title: {title} | text: {chunk}`, so **renaming a
document invalidates its vectors.** The hash must include the title.

---

## 6. Loading the other two stores

### Ontology (`ontology/`)

Loaded at **startup**, not ingested. Validated against a schema; the service **fails to boot** on a
malformed entry rather than starting degraded.

```bash
genie-ontology validate      # schema + cross-references (every metric's source api exists)
genie-ontology verify        # each metric definition vs the code that computes it  ← the hard one
genie-ontology render        # → kb/generated/*.md for the RAG long tail
genie-ontology diff          # what changed vs the deployed version
```

`verify` is the step that found the eight contradictions in `FINDINGS.md` §6. Run it in CI; treat a
definition that cannot be verified as a **failing test**, not a warning.

### Capability catalog (`catalog/`)

Loaded at startup; utterances embedded into `kb_capability` (a separate table from `kb_knowledge` —
a 67-character utterance and a 1,000-character policy paragraph have incomparable score
distributions).

```bash
genie-catalog validate       # schema, slots resolvable, execution path exists
genie-catalog audit          # authorization block vs the real controller annotation  ← F1
genie-catalog ingest         # embed utterances → kb_capability, bump catalog_version
```

`audit` is `FINDINGS.md` F1 as a build step: for every capability, confirm the declared gate matches
what the controller actually enforces. A drift here is the preflight-pass/backend-403 bug, and
catching it in CI is much cheaper than catching it in an alert.

---

## 7. Operating the knowledge base

| Task | How |
|---|---|
| Add a document | PR into `kb/`, CI validates, merge ingests |
| Fix a wrong answer | Find the chunk (`genie-kb search`), fix the source doc, PR |
| Retire a policy | `status: deprecated` + `effective_to` — never delete; old questions still need the old answer |
| Roll back a bad ingest | `git revert` the PR, re-run `ingest`; `kb_version` bumps again and caches clear |
| Check what Genie knows | `genie-kb search` from the terminal, or the Workbench's retrieval inspector |
| Find dead weight | Log retrieval hits per chunk; chunks never retrieved in 90 days are candidates for rewrite or removal |

**Track retrieval hits per chunk from day one.** It is the only honest signal about whether the
corpus is any good, and it is impossible to reconstruct retrospectively.

---

## 8. What I need from you

1. **Drop the `.md` files in `kb/inbox/`.** I'll triage them into ontology / catalog / kb / excluded
   and report the reasoning per file.
2. **Who owns Tier A?** The 15–25 policy documents are the difference between "Genie explains the
   screen" and "Genie explains the business". It is writing work, not engineering work, and it is on
   the critical path.
3. **Can we export resolved support tickets?** Tier E is the highest value per word in the whole
   corpus, because it is the real question distribution rather than the one we imagine.
4. **Are there client-specific documents** — contracts, custom rate cards? If yes, `client_scope`
   filtering becomes load-bearing in v1 and needs its own isolation eval before launch.
