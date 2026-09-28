# Build stages and test strategy

Supersedes `PLAN.md` §16, which was written before the design intake and the service audit.
Date: 2026-09-18.

---

## 1. What changed, and why the stages moved

| Since `PLAN.md` | Effect on the stages |
|---|---|
| Metric definitions already written in the deck (`FINDINGS.md` §2) | Ontology is **transcribed**, not authored — Stage 1 shrinks by weeks |
| `SimulateCampaignCost` exists (`SERVICES.md` §6) | Planner drops from "build a media-planning engine" to **allocator + reach model** |
| Genie prefills, never executes (`FINDINGS.md` §5) | The confirmation state machine mostly disappears |
| Seven unresolved contradictions (`FINDINGS.md` §6 + `SERVICES.md` §1) | A **Stage 0 that is not code** |
| `person_day` exists | The reach model is a data job that can start on day one, in parallel |

---

## 2. Four releases

```
R0  FOUNDATIONS        ontology · skeleton              nothing user-visible
R1  GENIE THAT KNOWS   navigate · explain · data        ◀ first demo
R2  GENIE THAT PLANS   reach · planner · calculator     ◀ the product
R3  GENIE THAT WATCHES advisors · RAG · hardening       ◀ the moat
```

**The demo you can show after R1 already sells the idea** — it explains every metric on every
screen, navigates the dashboard, and answers questions about live numbers. Do not wait for R2 to
show anyone.

### The twelve stages at a glance

| S | Stage | Rel | Needs | Delivers | Exit gate |
|:--:|---|:--:|---|---|---|
| **0** | **Unblock** *(no code)* | — | — | 7 rulings · 5 backend facts · access · 4 decisions | Nothing open can invalidate a downstream design decision |
| **1** | **`@adgrid/ontology`** | R0 | S0 | Screens · metrics · actions · gates, as data. **Ships the (i) tooltips** | Every CC + Launch metric modelled, 100 % verified against code |
| **2** | **Service skeleton** | R0 | C1, D1 | FastAPI · JWKS auth · sessions · SSE · tracing · CI · **Postgres + pgvector tables (empty)** | Authenticated request → stub block, fully traced |
| **3** | **NAVIGATE + EXPLAIN** | R1 | S1, S2 | Ontology lookup · intent routing · deep links | 100-query set passes; budget/batch collision 30/30; NAVIGATE p95 < 200 ms |
| **4** | **DATA path** | R1 | S3, C4 | Live BFF reads · provenance + `as_of` · numeric post-check | Numbers match the screen **exactly**, 50 spot-checks ◀ **demo** |
| **5** | **Reach model** | R2 | **C3 only** | `avg_frequency` lookup from `person_day` | Validated on held-out campaigns ◀ **starts at S1** |
| **6** | **Planner** | R2 | S5, B1–B3 | Allocator + reach + `simulate-cost` · back-test harness | Impressions MAPE ≤ 10 %, reach ≤ 20 % |
| **7** | **Calculator tab** | R2 | S6 | `POST /v1/genie/plan` · plan card · headroom advice | Deck's example reproduces; **works with the LLM off** |
| **8** | **Prefill bridge** | R2 | S6, S7 | Plan → wizard, variant-aware | All 3 variants prefill; nothing submits without a human click |
| **9** | **Advisors** | R3 | S4 | 9 watchers · ranking · dismissal · **role-gating** | ≥ 70 % "worth showing"; 0 proposing actions the role lacks |
| **10** | **RAG** | R3 | S2, D3 | Ingest CLI · Tier A corpus · EXPLAIN L-3 | recall@5 ≥ 0.85; 0 isolation leaks |
| **11** | **Hardening** | R3 | all | Caching · breakers · load · chaos · cost · eval gates in CI | p95 under load; a deliberately regressed build is blocked |

**Critical path:** `S0 → S1 → S2 → S4 → S6 → S7 → S8`, with **S5 running alongside from S1**.

**Runs in parallel:** S5 (from S1) · S9 (from S4, alongside S6–S8) · S10's corpus authoring (any
time — it is writing work, not engineering).

**pgvector:** tables created **empty in S2** with `halfvec(3072)` columns; populated in S8
(capability utterances) and S10 (knowledge base); HNSW index built **after** S10's bulk ingest, and
only once chunks pass ~5,000. Creating the columns while empty is a two-minute migration; converting
later needs a change window.

---

## 3. The stages

### Stage 0 — Unblock (no code)

Everything here is a decision, a lookup, or an access request. All of it is parallel, and none of it
needs an engineer on Genie.

#### A. The seven rulings — Design + Analytics

Each is a case where the product says two different things. Until each is settled, the ontology
entry cannot be written, because there is no single correct answer to record.

| # | Ruling needed | The conflict | Blocks |
|---|---|---|---|
| **R1** | **"Avg Attention" — ratio or duration?** Whichever keeps the name, the other gets renamed | Command Centre: **70 %** (`faces looking ÷ faces present`). `content-performance/definitions.md`: **4.8 s** (dwell-derived), and that doc flags it unresolved as *"decision #1"* | S1 · every attention answer |
| **R2** | **Dayparts — analytics or booking definition?** | Command Centre: Morning 6a–12p · Afternoon 12–5p · Evening 5–10p · Night 10p–6a (**24 h**). Launch wizard: 10AM–1PM · 1–4PM · 4–7PM · 7–10PM (**12 h**). Four identical labels, different ranges | S1 · S6 (the Planner allocates by daypart) · any "weight Evening" advice |
| **R3** | **Loop length — 60 s or 120 s?** | Avg Dwell annotation says *"on a 120s loop"*; Launch wizard says *"A 60-second loop holds 6 static (10s) or 4 videos (15s)"*. A direct multiplier on impressions → CPM → the entire Calculator | S1 · S6 |
| **R4** | **"Wallet Balance" on the Command Centre — which balance?** | CC **₹15,75,000** vs Wallet screen main **₹12,30,000** vs Total Credits **₹41,60,000** (main + ₹29,30,000 sub-wallets). AdGuide's own plan reserves *"from ₹12.30 L"*, agreeing with the Wallet screen | S1 · S6 (reservation) · A1/A3 advisors |
| **R5** | **"Inventory Management" or "Screen Management"?** | Command Centre and Launch wizard say Inventory; the Wallet frame nav says Screen. NAVIGATE resolves on these labels | S1 · S3 |
| **R6** | **Should a `CAMPAIGN_MANAGER` see Top up / Settle?** | The CM Command Centre shows both actions; `PermissionBundles.CAMPAIGN_MANAGER` grants neither `TOPUP_WALLET` nor `MARK_INVOICE_PAID`. Either the bundle is wrong or the screen is | S1 (capability auth) · S9 (advisor role-gating) |
| **R7** | **AD_PARTNER audience scoping — which aggregation serves the Command Centre's audience panel?** | `dashboard_views` is scoped by **`owner_id`**, but `ClientType.AD_PARTNER` has `ownsStores = false`. Yet the CC shows an AD_PARTNER Network Reach, Audience Profile and Location Distribution. **Cross-tenant question** | S1 · S4 · P7 (the Planner's footfall input) |

**R7 is the one to answer first.** It is the only ruling that is simultaneously a correctness
question, a cross-tenant question, and an input to the Planner.

**R3 has an authoritative answer in the data**, not in the design: the per-cell loop values in
`BillingCostCell`. Read it, then make the annotation match — do not ask the design team.

#### Two related items that are *not* rulings

| | Item | Why it isn't a ruling | Handled in |
|---|---|---|---|
| — | Campaign counts disagree on the Command Centre (42 active · "from 18 Campaigns" · annotation "Σ 42 campaigns") | Almost certainly a mock-data artifact, not a definition conflict | Verify against the seeded tenant in **S4** |
| — | The CM's AdGuide opener is byte-identical to the admin's, promising approvals the role cannot perform | An engineering fix, not a decision: the opener is **computed per role** from `UserContext.permissions` | Built in **S9**, evaluated as its own gate |

#### B. Backend lookups — no decision, just facts

| # | Question | Blocks |
|---|---|---|
| B1 | **`SimulateCampaignCost`: does it price a hypothetical selection, or need real device ids from `availability-search` first?** And what is its p95 latency? | **S6 design** — decides whether the allocator can iterate cheaply or needs a local pricing approximation with one authoritative call at the end |
| B2 | Which cost leg does the plan card show — `adgrid_to_ad_partner` or `ad_partner_to_brand`? The difference is the agency's margin | S6 · S7 |
| B3 | Is the plan's cost pre- or post-GST? Both are returned; the wallet is debited in one of them | S6 · S7 |
| B4 | Confirm `InitiateConfirmCampaign` / `CompleteConfirmCampaign` is the OTP two-step behind "Authorise selected campaigns" | S8 |
| B5 | Per-metric **freshness**: camera metrics refresh ~6 h via the batch; billing and campaign metrics are live. Which is which? | S1 (the `as_of` field) · S4 |

#### C. Access and environment

| # | Need | Owner | Blocks |
|---|---|---|---|
| C1 | Keycloak JWKS URL, "me" endpoint, client-hierarchy endpoint (`ARCHITECTURE.md` §9.1) | Backend | S2 |
| C2 | Test users, one per role, with obtainable JWTs | QA | everything |
| C3 | Query access to `person_day` in QA | Data | **S5 — the longest-lead item** |
| C4 | Seeded **adversarial** QA tenant (§5.4 — the negative and empty cases, not just populated data) | QA | S4 onward |

#### D. Your decisions

| # | Decision | Blocks |
|---|---|---|
| D1 | LLM + embedding provider and region — live customer figures transit it | S2 |
| D2 | Is the product called **AdGuide** or **Genie**? The deck says AdGuide throughout | S1 |
| D3 | Who authors the Tier A corpus — 15–25 policy documents (`KNOWLEDGE.md` §8) | S10 |
| D4 | Which roles get it in v1 (default: `AD_PARTNER_ADMIN` + `CAMPAIGN_MANAGER`) | S1 |

**Exit:** every R and B answered and recorded in this repo; C1–C4 usable by an engineer; no open
question can invalidate a downstream design decision.

---

### Stage 1 — `@adgrid/ontology`

Transcribe the deck's annotation cards into structured data, and verify every definition against the
code that computes it.

**Requires**

| Need | From | Owner |
|---|---|---|
| R1–R7, all seven rulings | S0-A | Design + Analytics |
| B5 — per-metric freshness (camera ~6 h vs live) | S0-B | Backend |
| D2 (product name), D4 (roles in v1) | S0-D | You |
| The Figma deck | have it | — |
| **Read access to the Java repos** to verify each formula against its aggregation | — | Backend |

**Builds** — `ontology/` YAML (screens · widgets · metrics · actions · gates · routes) · startup
validator · `genie-ontology` CLI (`validate` / `verify` / `render` / `diff`) · the F1 authorization
audit table · the `@adgrid/ontology` package

**Team** 1 engineer · design for transcription review · analytics for formula verification

**Exit** Every Command Centre and Launch-wizard metric modelled · **100 % verified against code** ·
tooltips rendering from the package in QA

> **Does not need the Genie service to exist.** S1 and S2 are both gated only on S0 and can run in
> parallel with different people.

---

### Stage 2 — Service skeleton

Boring infrastructure, correct. The point is the plumbing, traced end to end.

**Requires**

| Need | From | Owner |
|---|---|---|
| C1 — Keycloak JWKS URL, "me" endpoint, client-hierarchy endpoint | S0-C | Backend |
| C2 — test users, one per role, with obtainable JWTs | S0-C | QA |
| D1 — LLM + embedding provider and region | S0-D | You |
| Postgres instance · Redis instance · a deploy target | — | DevOps |

**Builds** — FastAPI app factory · pydantic config that fails to boot on a bad value · structured
logging · OTel tracing · CI · JWKS verification → `UserContext` · Redis sessions with Postgres
mirror · SSE transport · `ui_context` parse + validate · **Postgres schema including the empty
`halfvec(3072)` vector tables** · `/healthz` `/readyz` `/metrics` · the Workbench shell

**Team** 1–2 engineers · DevOps for Postgres, Redis, deploy

**Exit** An authenticated request returns a stub block with a full trace · CI green · provider swap
is one env var · vector tables exist and are empty

---

### Stage 3 — NAVIGATE + EXPLAIN

Ontology lookup only. No RAG, no BFF dependency — which is why it comes first: the largest
user-visible win with the fewest dependencies.

**Requires**

| Need | From | Owner |
|---|---|---|
| The ontology package | S1 | — |
| The running skeleton | S2 | — |
| **Intent golden set, ~250 labelled queries** | new | Ad ops + eng |
| Embedding provider live (for the kNN intent tier) | S2 | — |

**Builds** — session-first router · intent classifier (cache → embedding-kNN → small LLM) ·
NAVIGATE handler (no LLM past classification) · EXPLAIN handler over the ontology · `text` / `link` /
`citation` blocks · the eval harness and its CI gates

**Team** 1–2 engineers · someone to label the golden set

**Exit** 100-query set passes · zero uncited claims in 50 reviewed · budget/batch collision 30/30 ·
NAVIGATE p95 < 200 ms

> Build the golden set from **real** ad-ops questions. Synthetic sets overstate accuracy and will not
> contain the collision cases that actually break this.

---

### Stage 4 — DATA path

**Requires**

| Need | From | Owner |
|---|---|---|
| S3 | — | — |
| The metric → endpoint map (each metric's `source.api`) | S1 | Backend |
| C4 — seeded **adversarial** QA tenant (§5.4) | S0-C | QA |
| A real sample response per endpoint from that tenant | S0 | Backend |

**Builds** — BFF client with the user's JWT forwarded · metric resolution from ontology +
`ui_context` · provenance and **`as_of`** on every number · numeric post-check on narration ·
`metrics` / `table` / `chart` blocks · **L1 contract tests** (each endpoint still returns what the
ontology claims)

**Team** 1–2 engineers · QA for the tenant

**Exit** Numbers match the screen **exactly** on 50 spot-checks across 5 screens · every number
carries `source_api` + window + `as_of` · zero invented figures in 50 reviewed answers

> **R1 ends here. Demo it.** Genie explains every metric, navigates the dashboard, and answers
> questions about live numbers.

---

### Stage 5 — Reach model  *(starts alongside S1)*

A data job, not a service. The longest-lead item in the whole build.

**Requires**

| Need | From | Owner |
|---|---|---|
| **C3 — query access to `person_day` in QA** | S0-C | Data |
| ≥ 90 days of `person_day` history | — | Data |
| A list of completed campaigns with their actual delivery | — | Backend |

**Builds** — aggregation job over `person_day` · `avg_frequency` lookup table binned by (city,
screens bucket, flight-length bucket, daypart set) · validation harness against held-out campaigns

**Team** 1 data engineer

**Exit** Lookup table populated · frequency predictions validated on held-out campaigns

> **No product decisions required.** It needs one access grant and nothing else, which is exactly why
> it must not wait for S4.

---

### Stage 6 — Planner

Allocator + reach model + `simulate-cost` for pricing. **Do not reimplement pricing.**

**Requires**

| Need | From | Owner |
|---|---|---|
| The frequency lookup table | S5 | — |
| **B1 — does `simulate-cost` price a hypothetical selection? p95 latency?** | S0-B | Backend |
| B2 (which cost leg) · B3 (pre/post-GST) | S0-B | Backend + Finance |
| R2 (dayparts) · R3 (loop length) · R4 (which wallet balance) | S0-A | Design |
| P1–P8 endpoints confirmed and reachable | S0 | Backend |
| **6 months of completed campaigns** for the back-test | — | Backend |

**Builds** — eligible-pool resolution · unit economics per cell · **greedy allocator** behind an
`Allocator` seam · reach model integration · wallet reservation check · `Plan` object with `facts[]` ·
**back-test harness**

**Team** 1–2 engineers · **someone who prices inventory, to review the model before code is written**

**Exit** Impressions MAPE ≤ 10 %, reach MAPE ≤ 20 % over 6 months of completed campaigns. If reach
cannot meet it, the plan card ships reach as a **range**, not a point estimate.

---

### Stage 7 — Calculator tab

**Requires** S6 · FE capacity for the panel's second tab

**Builds** — `POST /v1/genie/plan` (**no LLM on this path**) · the deterministic form · the
"Recommended plan" card · rule-based headroom advice · narrator with numeric post-check

**Team** 1 engineer · 1 FE

**Exit** The deck's worked example reproduces within tolerance · **the endpoint works with the LLM
provider switched off**

---

### Stage 8 — Prefill bridge

**Requires**

| Need | From | Owner |
|---|---|---|
| S6, S7 | — | — |
| Specs for all **3 wizard variants** (Standard/Premium × Broadcasting/Targeted) | — | Design + FE |
| FE access to the wizard's form state | — | FE |
| B4 — confirm the OTP two-step behind "Authorise selected campaigns" | S0-B | Backend |

**Builds** — `prefill.launch_wizard` capability · plan → wizard field mapping, variant-aware ·
the FE bridge (route push + form seed) · the "Prefilled by AdGuide — everything stays editable" banner

**Team** 1 engineer · 1 FE

**Exit** All three variants prefill correctly · every plan value maps to a wizard field · **nothing
is submitted without a human click**

> **R2 ends here. This is the product.**

---

### Stage 9 — Advisors  *(runs parallel to S6–S8)*

**Requires**

| Need | From | Owner |
|---|---|---|
| S4 (the live-data path) | — | — |
| A1–A9 endpoints (`ARCHITECTURE.md` §9.3) | S0 | Backend |
| **R6 — can a CM see Top up / Settle?** | S0-A | Design |
| Thresholds per advisor, agreed with ad ops | — | Ad ops |
| A1–A9 note: A6 needs a **daily series**, not a window total | — | Backend |

**Builds** — 9 advisors as pure functions · ranking by severity × confidence × actionability ·
max-3 · dismissal memory (14 d per user/advisor/entity) · **role-gating from
`UserContext.permissions`** · `GET /v1/genie/suggestions` · the role-aware opener

**Team** 1 engineer · ad ops for thresholds and the "worth showing" rating

**Exit** Numeric post-check 100 % · ad ops rate ≥ 70 % of suggestions "worth showing" · **zero
advisors proposing an action the role cannot perform**

---

### Stage 10 — RAG, the long tail

**Requires**

| Need | From | Owner |
|---|---|---|
| Empty vector tables | S2 | — |
| **D3 — a Tier A corpus author** | S0-D | You |
| **The Tier A corpus written** (15–25 policy documents) | — | Business |
| Your `.md` files triaged out of `kb/inbox/` | — | Me + you |
| Retrieval golden set (~150 query→chunk pairs) | — | Ad ops + eng |
| Resolved support tickets, if exportable (Tier E) | — | Support |

**Builds** — `genie-kb` CLI · the 10-step ingest pipeline · hybrid search (vector + tsvector, RRF) ·
pre-ANN metadata filters · confidence gate · EXPLAIN's L-3 leg · **HNSW index after bulk ingest**

**Team** 1 engineer · the corpus author (**not** an engineer)

**Exit** recall@5 ≥ 0.85 · **0 audience-isolation leaks · 0 client-scope leaks** · refusal
calibration ≥ 0.90

> Corpus authoring has **no code dependency** and can start on day one. Do not let it become the
> thing that delays R3.

---

### Stage 11 — Hardening

**Requires** Everything above · a load-test environment · production-like data volume

**Builds** — cache layers L0–L4 · single-flight · circuit breakers per capability · load test ·
chaos drills · cost dashboards per stage · every eval gate wired into CI as blocking

**Team** 1–2 engineers · DevOps

**Exit** p95 SLOs met under load · every chaos drill degrades cleanly · **a deliberately regressed
build is blocked by the eval gate**

---

## 4. Dependency graph and sequencing

```
S0 ═══╦══════════════════════════════════════════════════════════ 7 rulings · 5 facts · access
      ║
      ╠═► S1 Ontology ═══╦═════════════════════════════════════════ the backbone
      ║                  ║
      ╠═► S2 Skeleton ═══╬═► S3 Navigate+Explain ═► S4 Data ══╦════ ◀ R1 DEMO
      ║        ║         ║                                    ║
      ║        ║         ║                                    ╠═► S9 Advisors ──────┐
      ║        ║         ║                                    │                     │
      ╚═► S5 Reach ══════╩═════════════► S6 Planner ═► S7 Calc ═► S8 Prefill ◀ R2   │
               ▲                                                                    │
       needs only C3                                                                │
                                                                                    │
           S2 ═════════════════════► S10 RAG ════════════════════════════════════════╪═► S11
                                        ▲                                           │  Hardening
                            corpus authoring (no code dep, start day one) ───────────┘
```

**Critical path:** `S0 → S1 → S2 → S4 → S6 → S7 → S8`

**Rules:**

- **S1 and S2 are parallel** — both gated only on S0, and they need different people (ontology needs
  design + analytics; the skeleton needs backend + DevOps).
- **S5 starts with S1**, not after S4. It needs one access grant and no product decisions, and the
  Planner cannot exist without it.
- **S3 and S4 may overlap. S6 and S7 may not** — the Calculator is a thin skin on the Planner.
- **S9 runs parallel to S6–S8.** Advisors are pure functions over BFF data and need nothing from the
  Planner except `ActionRef`.
- **S10's corpus authoring starts whenever someone can write it.** The engineering half is small; the
  writing half is the long pole.
- **Nothing before S1 is finished.** Every later stage reads the ontology.

### Who you need, by stage

| Role | Needed in |
|---|---|
| Backend engineer (Adgrid) | S0 (answers) · S1 (verification) · S2 (endpoints) · S4 · S6 · S9 |
| Genie engineer(s) | S2 → S11 |
| Data engineer | **S5** (can be one person, part-time, starting immediately) |
| Frontend engineer | S7 · S8 · integration |
| Design | S0 (rulings) · S1 (transcription review) · S8 (wizard variants) |
| Analytics | S0 (R1, R2, R7) · S1 (formula verification) |
| Ad ops | S3 (golden set) · S9 (thresholds + rating) · S10 (golden set) |
| QA | S0 (tenant + test users) · S4 onward |
| Business writer | **S10 corpus — not an engineer, and startable today** |
| DevOps | S2 · S11 |

---

## 5. How to test before touching the QA dashboard

The premise of Genie is that it is embedded — so "test it without the dashboard" is the real
question. Answer: **build a Workbench that stands in for the dashboard**, and make the FE integration
the last thing that changes, not the first.

### 5.1 The Workbench — the single highest-value test artifact

A standalone page served by the Genie service itself at `/dev/workbench`, dev-only, behind a flag.

```
┌────────────────────────────────────────────────────────────────────┐
│ GENIE WORKBENCH                         [JWT ▾ admin | cm | brand] │
├──────────────────────────┬─────────────────────────────────────────┤
│ ui_context               │  Panel (the real GeniePanel component)  │
│                          │                                         │
│ screen  [command_centre ▾]│  ● Viewing Home · Overview             │
│ route   /command-centre  │                                         │
│ entities {client_id: …}  │  [chat renders here, real blocks]       │
│ filters  {period: …}     │                                         │
│ selection [wallet_bal ▾] │                                         │
│                          │                                         │
│ [ emit context ]         │  ─────────────────────────────────────  │
├──────────────────────────┤  RAW BLOCKS   │  TRACE   │  COST        │
│ Scenario: ▾              │  {json}       │  spans   │  tokens/₹    │
│  · wallet runway low     │               │          │              │
│  · 7 pending approvals   │               │          │              │
│  · campaign underdeliver │               │          │              │
└──────────────────────────┴───────────────┴──────────┴──────────────┘
```

Four things it gives you that the QA dashboard cannot:

1. **Every screen's context on demand**, including ones the dashboard hasn't built yet.
2. **Role switching in one click** — the fastest way to catch `FINDINGS.md` §6.7-class bugs.
3. **Raw blocks + trace + cost side by side** with the rendered answer.
4. **Scenario presets** that force an advisor to fire without waiting for real data to drift.

**Critical:** the Workbench emits `ui_context` built from the *same* `@adgrid/ontology` package the
FE will use. If it hand-rolls its own context shape, you are testing a fiction.

### 5.2 The seven test levels

| | Level | What it catches | Runs |
|---|---|---|---|
| **L0** | **Unit** — planner, advisors, slot validators, chunkers | Logic bugs in pure functions. Everything that matters in Genie is a pure function by design, so this layer is unusually powerful here | every commit |
| **L1** | **Contract** — each BFF endpoint the ontology names still returns the fields it claims | Backend drift. This is what turns a silent `null` into a red build | every commit + nightly vs QA |
| **L2** | **Eval** — golden sets with the gates from `PLAN.md` §14 | Model regressions, prompt regressions, retrieval regressions | every commit; blocks merge |
| **L3** | **Back-test** — planner vs delivered campaigns | A planner that cannot retrodict | on planner change |
| **L4** | **Workbench** — human, interactive | Everything a human notices and no assertion encodes: tone, ordering, whether the answer is *useful* | continuous |
| **L5** | **Shadow** — Genie runs in the QA dashboard, hidden; logs what it *would* have said | Real `ui_context` from real navigation, real data drift | once FE lands |
| **L6** | **Canary** — one internal team, panel visible, feedback button | Whether people actually open it twice | pre-release |

### 5.3 Getting real data without the dashboard

Genie needs the BFF, not the dashboard. Three options, in order:

1. **Point at QA BFF with a test user's JWT.** Best — real responses, real auth, real latency. Needs
   the Stage 0 seeded tenant and test users.
2. **Record–replay.** Capture QA BFF responses once into fixtures; replay them in CI so tests are
   deterministic and offline. **Refresh fixtures nightly against QA**, and fail the build when a
   response shape changes — that is L1.
3. **Fixtures only.** Fine for L0/L2, never sufficient for L4 — hand-written fixtures agree with
   your assumptions, which is exactly what you are trying to test.

Use 1 for the Workbench, 2 for CI, and never rely on 3 alone.

### 5.4 The seeded tenant matters more than it sounds

Ask QA for a tenant that is **deterministic and adversarial**, not just populated:

- one brand with a campaign **underdelivering**, one pacing fine, one overspending
- a wallet with **~6 days runway** (so `wallet_runway` fires)
- a sub-wallet that is **short** (so `subwallet_short` fires)
- ≥3 campaigns **pending approval**, one waited >3 days
- a device with **uptime < 90%** carrying an active campaign
- at least one brand with **no data at all** — the empty-state path is where assistants embarrass you

Without the negative and empty cases you will ship an assistant that has only ever seen healthy data.

### 5.5 Integration is the last change, not the first

By the time the panel goes into the QA dashboard, the only genuinely new things are: the real
`ui_context` publisher, the block renderer against your design system, and the prefill bridge. Everything
else has been exercised for weeks in the Workbench. **Land it behind a feature flag, run L5 shadow
mode for a week, then reveal to the canary group.**

---

## 6. Definition of done, per stage

A stage is done when: its exit criterion is measured (not asserted), its evals are in CI and
blocking, the Workbench can demonstrate it end to end, and the docs in this repo reflect what was
actually built rather than what was planned.
