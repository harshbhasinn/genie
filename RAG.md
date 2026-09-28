# Genie — Retrieval Design

**Companion to `PLAN.md`. Where they disagree, this one wins** (it is later and measured).
Date: 2026-09-16.

---

## 0. Verdict

**Yes — but RAG is the third retrieval mechanism, not the first, and the corpus that would make it
valuable does not exist yet.**

Two claims, both measured, both in §2:

1. Most of what Genie must explain is **structured**, and structured content should be looked up,
   not embedded. Vector search over metric definitions will confidently return the wrong metric,
   and I can point at the exact collision in your product where it will happen.
2. The 66 markdown files in `C:\Work\Adgrid\AWS` (76,915 words) are **engineering** documentation —
   handoffs, build plans, conversation logs. Pointed at a RAG index they would make Genie quote
   renamed enums, mocked values and unratified proposals to your customers. That is not a corpus
   that needs better retrieval; it is a corpus that needs to not be indexed.

So the recommendation is: **build the retrieval ladder now, build the RAG plumbing in Phase 3, and
build the business corpus deliberately rather than scavenging it.** RAG earns its place on the long
tail — policy, billing rules, contract terms, SOPs — which is real and growing, but is roughly 15%
of what users will ask, not 80%.

---

## 1. The retrieval ladder

Three mechanisms, ranked by precision. **Always try them in this order. Never skip up.**

| | Mechanism | Answers | Precision | Latency |
|---|---|---|---|---|
| **L-1** | **Ontology lookup** (exact, keyed) | "What does budget utilization mean?" · "What can I do on this screen?" · "Where do I change a rate card?" | **Exact — cannot return the wrong metric** | ~5ms |
| **L-2** | **Capability retrieval** (semantic over utterances) | "pause this" → `campaign.pause` | High, gated by ambiguity check + "none of these" | ~40ms |
| **L-3** | **RAG** (hybrid over prose corpus) | "How does settlement work for revenue-share batches?" · "What's the cancellation policy on a locked batch?" | Approximate — needs citation + confidence gate | ~80ms + LLM |

Plus two planes where **retrieval is forbidden** (`PLAN.md` §5):

- **Live data** → BFF call, every time. Never the vector store.
- **Recommendations** → deterministic advisor code. Never the vector store.

### The routing rule

```
question
  ├─ names a metric, widget, screen, or capability in the ontology?   → L-1, done
  ├─ asks to DO something?                                            → L-2
  ├─ contains a possessive / entity / date range ("my", "Nykaa",
  │   "last week")?                                                   → live data (plane C)
  ├─ asks for a recommendation?                                       → advisors
  └─ otherwise (policy, process, "how does X work")                   → L-3 RAG
```

**L-1 first is the single highest-leverage decision in this document.** It converts the most
common and most trust-critical class of question — "what does this number on my screen mean?" —
from a probabilistic retrieval problem into a dictionary lookup, at 1/10th the latency and zero
hallucination risk. Most dashboard assistants get this backwards, embed their own documentation,
and spend the next six months tuning a reranker to fix a problem they created.

---

## 2. The evidence

### 2.1 Why not to embed metric definitions

Your product ships both of these endpoints:

```
GET /api/v1/overview/batch-utilization
GET /api/v1/campaigns/budget-utilization
```

**"Batch utilization"** and **"budget utilization"** are one token apart lexically and adjacent
semantically. Any embedding model will score them within noise of each other. A user on the
campaign screen asking *"what's utilization here?"* has a real chance of getting the batch
definition — fluently worded, correctly cited, and **wrong about a number they are looking at**.

There is no reranker that reliably fixes this, because the chunks genuinely are near-identical in
meaning. There is, however, a trivial fix: `ui_context.visible_metrics` says
`budget_utilization_pct` is on screen, the ontology has exactly one entry under that key, and the
answer is a lookup. **Ambiguity that context can resolve should never be handed to a vector index.**

The same collision class exists across your surface: `VIEW_CLIENT_RATES` vs `VIEW_BATCH_RATES`,
`ASSIGN_BATCH` vs `DEPLOY_BATCH`, brand-level vs client-level spend.

### 2.2 Why not to index the existing docs

Measured: **66 markdown files, 76,915 words.** The largest are `VOICE_HANDOFF.md` (6,106),
`voice-dashboard-spec.md` (5,283), `Knowledge.md` (5,261), and build plans. Three concrete traps
found by reading two of the most business-relevant files:

| Trap | Found in | What Genie would say |
|---|---|---|
| **Stale enum** | `agency-dashboard-apis.md` returns `"type": "AD_AGENCY"` — but `ClientType` records that `AD_AGENCY` was **renamed to `AD_PARTNER` in Phase 4** | Genie uses a client type that no longer exists |
| **Mocked value documented as behaviour** | Same file: *"Balance is mocked as `0` pending billing-service wallet integration"* | Genie tells a user their wallet balance is ₹0 |
| **Unratified proposal read as fact** | `content-performance/definitions.md` proposes *"Avg attention (s) = dwellSumMs / dwellN / 1000"* and explicitly labels it **"This is decision #1"** — an open question, not a ruling | Genie states a metric definition that was never agreed |

All three are *correct* documents. They are engineering artifacts doing their job. They are simply
not statements of product truth, and a vector index cannot tell the difference.

**This is what `status: published | draft` in the metadata contract (§5) is actually for.** It is
correctness, not hygiene.

### 2.3 What the existing docs are good for

Not nothing — the opposite. `content-performance/definitions.md` is **exactly the raw material for
the ontology**: metric → formula → the trap. Its most valuable passage is a warning that the UI
label "AVG ATTN — 4.8s" maps to `dwell_sum_ms/dwell_n`, while the similarly-named `attn_sum/attn_n`
is a 0–1 score and *not* seconds.

That is precisely the kind of fact Genie exists to carry — and it belongs in a **structured
`gotchas:` field on one metric**, reviewed and dated, not as a floating chunk that may or may not
surface. **Mine these docs into the ontology; do not index them.**

---

## 3. What must never enter the corpus

| Never | Why |
|---|---|
| Live metric values, any BFF response | Goes stale silently; there is no TTL that makes this safe |
| Campaign / brand / store / device **names as content** | Entity data changes daily. Genie would cite deleted campaigns. Entity resolution is an API call, always |
| Anything client-scoped, without a `client_id` filter | One mis-filtered query is a cross-tenant leak |
| Internal architecture, ports, buckets, gRPC topology | `Knowledge.md` is superb and belongs to engineers. An AD_PARTNER_ADMIN must never retrieve it |
| Credentials, connection strings, tokens | Obvious, and worth an ingest-time scanner that fails the build |
| PII from any source | Redaction at ingest, not at query |
| Conversation logs, handoffs, build plans | Point-in-time engineering state; wrong by construction within weeks |

**Default is deny.** A document enters the corpus because someone tagged it `audience:` and
`status: published`, not because it was in the folder.

---

## 4. Corpus design

Two indexes plus one lookup table. Keeping them separate matters: a 67-character utterance and a
1,000-character policy paragraph do not have comparable similarity score distributions, so a shared
ANN index makes one threshold serve two scales badly.

| Store | Kind | Contents | Consumer |
|---|---|---|---|
| **`ontology`** | **Postgres tables, no vectors** | Screens, widgets, metrics, actions, routes | L-1 lookup |
| **`kb_capability`** | pgvector | One row per capability utterance | L-2 selection |
| **`kb_knowledge`** | pgvector | The prose corpus below | L-3 RAG |

### `kb_knowledge` — source tiers

| Tier | Source | Status | Owner |
|---|---|---|---|
| **A. Product & policy** | Billing/settlement rules, rate card policy, batch lifecycle, contract & SLA terms, deployment plan rules, cancellation policy | **Does not exist — must be written** | Business |
| **B. Operational** | Ad ops SOPs, runbooks, troubleshooting ("why is a device offline?"), onboarding guides | Partly tribal | Ad ops |
| **C. Generated from ontology** | Metric definitions rendered to prose | Generated, Phase 1 | Pipeline |
| **D. Release notes** | "What changed in the dashboard?" | Does not exist | Eng |
| **E. Support FAQ** | Mined from real tickets | Exists as tickets | Support |
| **F. Mined engineering docs** | Hand-curated extracts only, re-verified against code | 66 files, mostly excluded | Eng |

**Tier A is the gap, and it is a writing problem, not an engineering one.** Roughly 15–25 documents.
Until it exists, L-3 has little to retrieve and Genie leans on L-1/L-2 — which is fine, and is why
RAG is Phase 3 and not Phase 1.

**Tier C is why the corpus is generated, not written twice.** The ontology is the source of truth;
its prose rendering is a build artifact. Regenerate on version bump; never hand-edit.

---

## 5. Chunking

Chunk quality sets the ceiling on retrieval quality. No reranker rescues a chunk that is half a
table and half a page footer.

| Source | Strategy | Size |
|---|---|---|
| **Ontology (Tier C)** | **One chunk per atom** — one metric, one widget, one screen. Never size-based | Natural |
| **Capability utterances** | One chunk per utterance, carrying the parent's full metadata | ~60–80 chars |
| **Prose (Tiers A/B/D/E)** | Heading-aware semantic split; never split mid-section | 800–1,200 chars, 150 overlap |
| **Tables** | **Whole, never split.** Rendered to markdown, section heading prepended | Whole |
| **Policy with effective dates** | One chunk per clause, each carrying `effective_from` / `effective_to` | Clause |

### Contextual headers — do this from day one

Before embedding, prepend the document title and section path to every chunk:

```
[Billing Policy > Revenue-Share Settlement > Timing]
Settlement for revenue-share batches runs on the 7th of the following month…
```

A chunk that says *"this runs on the 7th of the following month"* is unretrievable on its own and
ambiguous once retrieved. This costs nothing, is a materially large recall win, and cannot be
retrofitted without a full re-embed.

**One chunk per atom for structured sources** is the other rule that cannot be retrofitted cheaply.
Size-based chunking of a metric entry splits the definition from its `gotchas`, which is exactly the
half that matters.

---

## 6. The metadata contract

**This is the part that cannot wait, and the part that is correctness rather than optimization.**
Declared as a typed object, validated at ingest *and* at startup, failing loudly on mismatch.

```python
@dataclass(frozen=True)
class ChunkMeta:
    # identity
    doc_id: str
    chunk_id: str
    section_path: list[str]
    source_kind: Literal["ontology", "policy", "sop", "faq", "release_note", "capability"]

    # ACCESS — pre-ANN filters. Correctness, not relevance.
    audience: frozenset[PlatformRole]     # who may retrieve this at all
    client_scope: str | None              # None = global; else must match a client the user can access

    # TRUST
    status: Literal["published", "draft", "deprecated"]   # only published is retrievable
    verified_at: date
    verified_against: str | None          # the code/doc this was checked against
    owner: str

    # TIME
    effective_from: date | None
    effective_to: date | None

    # VERSIONING — every one of these goes in the cache key
    kb_version: str
    ontology_version: str | None
```

### The four filters that run *before* the ANN search

1. **`audience`** — an AD_PARTNER_ADMIN must never retrieve an ADOPS-internal runbook. Post-filtering
   is not equivalent: filter after retrieval and top-K silently shrinks to nothing, giving a
   confident "I don't know" on questions Genie can actually answer.
2. **`client_scope`** — a client-specific contract clause is filtered by `client_id`, or it is a
   cross-tenant leak. One bug here is a customer incident.
3. **`status == published`** — §2.2's three traps all die here.
4. **`effective_from/to`** — a question about last quarter's invoice must cite the policy **in force
   then**. Rate cards change; answering from the current policy is wrong and very hard to spot.

**If you skip this contract:** real docs get ingested without `audience`, the filter matches
everything (or nothing), and you find out from a customer who saw another tier's documentation. A
startup assertion turns that into a deploy failure instead.

---

## 7. The embedding contract

One frozen module-level constant, shared by ingest and query. Chotu's most expensive silent bug
(`FINDINGS.md` §1) was the two sides drifting apart — no error, just quietly worse matches.

```python
# app/retrieval/constants.py — FROZEN. Changing either value requires a full re-embed
# and a kb_version bump. Both sides read from here; neither may inline a string.
QUERY_PREFIX    = "task: search result | query: "
DOCUMENT_FORMAT = "title: {title} | text: {chunk}"
```

- **Never truncate dimensions.** Stay at the model's native width and use `halfvec` (§8). Truncating
  later means re-embedding — cheap in money, a real ingestion outage in practice.
- **The document side includes the title**, so *renaming a document invalidates its vectors*.
  Encode that in the ingest pipeline rather than discovering it.
- A startup self-check embeds one known string and asserts cosine ≈ 1.0 against its stored vector.
  Cheap, and it catches a provider or model swap before users do.

---

## 8. Storage and index

pgvector, in Genie's **own database** — not shared with `chotu_rag`/`vector_qa`. Different product,
different tenancy, different lifecycle. Reuse the pattern, not the instance.

```sql
CREATE TABLE kb_knowledge_chunks (
    id            BIGSERIAL PRIMARY KEY,
    doc_id        TEXT NOT NULL,
    chunk_id      TEXT NOT NULL UNIQUE,
    content       TEXT NOT NULL,          -- with contextual header, as embedded
    embedding     HALFVEC(3072),          -- halfvec from day one, not vector
    meta          JSONB NOT NULL,         -- ChunkMeta, §6
    tsv           TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);

CREATE INDEX ON kb_knowledge_chunks USING GIN (tsv);
CREATE INDEX ON kb_knowledge_chunks USING GIN (meta jsonb_path_ops);
-- HNSW deliberately deferred — see below.
```

**`halfvec` from day one.** pgvector caps HNSW/IVFFlat on `vector` at 2,000 dimensions; `halfvec`
raises it to 4,000. At 3072 dims a `vector` column can *never* be indexed. Chotu paid to learn this
and the migration is a column cast that takes two minutes while the table is empty and needs a
change window once it isn't. Do it now; there is no cost.

**Build HNSW *after* bulk ingest, not before.** Loading rows into a live HNSW index is substantially
slower than building it once at the end. Expect **no speed-up** at launch volume — the planner may
not even use it — and that is correct, not a broken migration.

**Flip when:** chunks pass ~5,000, or p95 retrieval passes 50ms.

```sql
-- after the corpus is loaded
CREATE INDEX CONCURRENTLY idx_kb_knowledge_hnsw
  ON kb_knowledge_chunks USING hnsw (embedding halfvec_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

Raise `maintenance_work_mem` before building. Set `hnsw.ef_search` per query, starting at 40.

**Baseline recall before indexing.** A sequential scan is exact KNN — 100% recall by definition.
HNSW is approximate, so recall will drop slightly when you index, and you need the pre-index number
to know how far to tune `ef_search` back. Easy now; impossible to reconstruct later.

**The seam:** every piece of SQL mentioning a vector lives in **one repository class**. No `<=>`
operators, no `::halfvec` casts, no distance literals anywhere else. This is what makes the index
migration one file instead of twenty.

---

## 9. The retrieval pipeline

A list of stages, so later additions **insert** rather than restructure. Optional stages are no-ops
at first.

```
query
  → normalize
  → embed (L1 cache)
  → FILTER   audience · client_scope · status · effective date · kb_version   [pre-ANN, §6]
  → SEARCH   vector top-20  ∥  BM25/tsvector top-20
  → FUSE     Reciprocal Rank Fusion, k=60
  → RERANK   score threshold + MMR for diversity        [cross-encoder: later, only if eval earns it]
  → GATE     confidence check                           [§10]
  → TOP-N    5 chunks to synthesis
```

### Hybrid search is ON in v1 — a deliberate difference from Chotu

Chotu deferred hybrid to target scale. Genie should not, because **this corpus is entity-dense from
day one**: metric names, permission names (`MANAGE_BRANDS_UNDER_AGENCY`), batch ids, rate card
names, status enums. Vector search is good at meaning and bad at literal strings, and a user asking
about `EDIT_CLIENT_RATES` by name is asking a literal-string question.

Postgres gives you the tsvector and GIN index for free, RRF is ~20 lines, the cost is ~5ms, and the
typical gain is +10–15 points of recall@5 on exactly this kind of query. The reason to defer it —
"we don't have noise yet" — doesn't apply when the corpus is a glossary.

**Reranking stays a no-op until the eval says otherwise.** A cross-encoder costs ~100ms and earns it
only once there is real noise to cut. Add it when recall@5 says so, not before.

---

## 10. The grounding contract

Everything below is non-negotiable and cheap. Skipping any of it is how a RAG feature becomes a
liability.

**Confidence gate.** If the top score is below `RETRIEVAL_MIN_SCORE`, **or** the top-1/top-2 gap
shows no clear winner, do not call the synthesis LLM at all. Return a templated *"I don't have that
in my knowledge base"* plus a deep link to the relevant screen and an escalation offer. Negative-cache
it briefly.

> Weak matches reaching the LLM is the single largest source of hallucinated product claims. The
> model will answer anyway — that is what it is for. The gate, not the prompt, is what stops it.

**Citations are mandatory and checked.** Synthesis receives numbered chunks and must cite chunk ids.
A post-check flags or strips any claim with no citation. The FE renders citations as
`{ "type": "citation", "sources": [...] }` blocks (`PLAN.md` §10) — a user who can check you is a
user who will trust you.

**Chunks are untrusted input.** Delimited, labelled as data, never granted instruction authority.
This matters more here than in a generic RAG app: your corpus will eventually include client-supplied
text, and campaign/brand names are user-editable. A brand literally named
*"ignore previous instructions"* is a realistic thing to find in an ad platform.

**Never let RAG answer a data question.** If retrieved text contains a figure, it is a worked example
and must be labelled as one at ingest. The routing rule in §1 is the primary defence; this is the
second.

---

## 11. Sizing — measured, not guessed

| Source | Estimate | Basis |
|---|---|---|
| Ontology (Tier C) | ~400 chunks | ~40 screens × ~10 atoms |
| Capability utterances | ~400 chunks | ~65 capabilities × ~6 utterances |
| Existing docs, if curated | **~80 chunks** | 76,915 words total, of which maybe 15k survives curation |
| Tier A policy (to be written) | ~200–400 chunks | 15–25 documents |
| **Total at launch** | **~1,100–1,300 chunks** | |

Two honest conclusions:

1. **The index is irrelevant at this size.** A sequential scan over 1,200 chunks is ~7ms. HNSW would
   change nothing. The `halfvec` column type is worth doing now only because it is free now and
   awkward later.
2. **Retrieval quality is therefore determined entirely by chunking and metadata** — §5 and §6 — not
   by the index, the reranker, or the embedding model. Effort should go there. This is the opposite
   of where most teams spend it.

---

## 12. Ingestion and lifecycle

```
source (git-tracked markdown + ontology YAML)
  → validate frontmatter against ChunkMeta       ← fails the build on missing audience/status
  → secret + PII scan                            ← fails the build
  → chunk per §5
  → prepend contextual header
  → embed (batched, cached by content hash)
  → upsert by chunk_id, soft-delete the absent
  → bump kb_version → invalidate L2/L3 cache atomically
```

- **The corpus lives in git**, reviewed like code. A policy change is a PR, not a CMS edit. This is
  what makes `verified_at` and `owner` meaningful.
- **Re-embed only changed chunks**, keyed by content hash — a full re-embed of 1,200 chunks is cheap,
  but the habit matters at 20,000.
- **`kb_version` bump is the only invalidation mechanism.** No manual cache flushing.
- **Log the chunk-length distribution every ingest and flag outliers.** A 40-character chunk or a
  4,000-character one is a chunking bug, and this is the cheapest detector there is.

---

## 13. Evaluation

Retrieval-specific gates, feeding `PLAN.md` §14.

| Eval | Metric | Gate | Set |
|---|---|---|---|
| **Ladder routing** | Question routed to the right mechanism (L-1 / L-2 / L-3 / live / advisor) | **≥ 0.97** | ~200 |
| **Metric-name collision** | `budget` vs `batch` utilization answered correctly | **1.00** | ~30 adversarial |
| Knowledge recall@5 | Right chunk in top 5 | ≥ 0.85 | ~150 query→chunk |
| Capability recall@20 | Right capability retrieved | ≥ 0.98 | ~200 |
| **Audience isolation** | A role retrieving a chunk it may not see | **0** | ~50 adversarial |
| **Client-scope isolation** | Cross-tenant chunk retrieved | **0** | ~50 adversarial |
| Faithfulness | Every claim cited; no uncited claims | **1.00** | ~150 |
| Refusal calibration | Correct "I don't know" when the corpus lacks it | ≥ 0.90 | ~80 |
| Effective-dating | Time-scoped question cites the policy in force then | ≥ 0.95 | ~30 |

**Ladder routing is the headline metric**, because every other failure mode in this document is
downstream of routing a question to the wrong mechanism.

Build the golden sets from **real questions** — ask ad ops for the 100 they actually get. Synthetic
sets overstate accuracy badly, and the collision cases in §2.1 are precisely the ones a synthetic
generator will not think to produce.

---

## 14. What this changes in `PLAN.md`

| Change | Where |
|---|---|
| Plane A is a **ladder**, not "RAG". L-1 ontology lookup is the primary path | §5 |
| RAG plumbing moves to **Phase 3**; Tier A corpus authoring is a parallel non-engineering workstream | §16 |
| Hybrid search is **v1**, not deferred — the corpus is entity-dense | new |
| `halfvec(3072)` from day one; HNSW deferred until after bulk ingest | new |
| Metadata contract (`audience`, `client_scope`, `status`, effective dating) validated at startup | §11 |
| Ladder-routing accuracy added as a gated eval at ≥0.97 | §14 |
| Existing 66 engineering docs are **mined into the ontology, not indexed** | §17 |

---

## 15. Open questions

1. **Who writes Tier A?** 15–25 policy/process documents is the gap between "Genie answers screen
   questions" and "Genie answers business questions". It is a business writing task with an
   engineering deadline, and it should start now — it is on the critical path for Phase 3 and
   nothing about it depends on code.
2. **Are there client-specific documents** (contracts, custom rate cards) that Genie should answer
   from? If yes, `client_scope` filtering becomes load-bearing in v1 rather than v2, and it needs a
   dedicated isolation eval before launch.
3. **Support ticket access** — Tier E is the highest-value corpus per word written, because it is
   the real question distribution. Can we export resolved tickets?
4. **Embedding provider and region** — same open question as `PLAN.md` §18 #4, and it binds here
   first: ingestion is when real metric definitions and policy text leave the building.
5. **Does `chotu_rag`'s ingestion service generalise?** If it is product-agnostic, Genie should reuse
   the service and not the database. Worth an hour of reading before building a second ingester.
