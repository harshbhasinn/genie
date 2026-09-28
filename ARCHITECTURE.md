# Genie — Architecture

How Genie is built, how it recommends, and how it embeds into the dashboard.
Companion to `PLAN.md`, `RAG.md`, `FINDINGS.md`. Date: 2026-09-17.

---

## 1. Two engines, not one

"Recommendation engine" covers two genuinely different things in this product, and the deck shows
both. Building them as one component is the mistake to avoid.

| | **The Planner** | **The Advisors** |
|---|---|---|
| Question | *"What should I create?"* | *"What needs my attention?"* |
| In the deck | Calculator tab → **"Recommended plan"** | **"Needs your attention"** strip · **"Low Balance Alert"** · AdGuide's opener |
| Mechanism | Constrained **optimisation** over inventory | **Threshold rules** over live state |
| Input | User constraints (budget, days, reach, mix) | Current campaigns, wallet, devices, approvals |
| Output | One synthesised plan + alternatives | 0–N ranked findings, each with an action |
| Cadence | On demand, ~1–3s | On page load, cached ~5 min |
| Fails by | Producing an unbuyable plan | Crying wolf |
| Correctness test | Back-test against delivered campaigns | Precision on a labelled "was this worth showing?" set |

Neither uses an LLM to produce a number. The LLM's only jobs are **parsing** the request into
constraints and **narrating** the result — both bounded, both checked.

```
                    ┌────────────────────────────────┐
  "plan a 2.5L      │                                │
   Diwali campaign" │   LLM: parse → constraints     │  bounded: schema-validated
        ──────────▶ │                                │
                    └───────────────┬────────────────┘
                                    ▼
                    ┌────────────────────────────────┐
                    │   PLANNER (pure code)          │  deterministic, testable,
                    │   inventory · rates · reach    │  back-tested
                    └───────────────┬────────────────┘
                                    ▼  Plan{facts[], numbers}
                    ┌────────────────────────────────┐
                    │   LLM: narrate                 │  bounded: may use no number
                    │                                │  absent from facts[]
                    └────────────────────────────────┘
```

---

## 2. The Planner — how it actually computes

### 2.1 Reverse-engineering the deck's own example

The deck gives one fully worked plan, which is enough to recover the model:

> Broadcasting · 30 days · ₹2.50 L budget · Image·10s · Balanced mix · target reach 4.0 L
> → **Screens 1,818 · Impressions 24.8 L · Blended CPM ₹101 · Daily burn ₹8k/day · Reach 8.3 L · freq 3.0×**

Checks that hold:

```
cost      = 2,480,000 / 1,000 × ₹101   = ₹2,50,480     ≈ ₹2,50,084 shown  ✓
daily burn= ₹2,50,084 / 30             = ₹8,336/day    ≈ ₹8k/day shown    ✓
reach     = impressions ÷ frequency
          = 2,480,000 ÷ 3.0            = 8.27 L        ≈ 8.3 L shown      ✓
per screen= 2,480,000 / (1,818 × 30)   = 45.5 impressions/screen/day
```

And 45.5/screen/day reconciles with the network constants:

```
footfall/screen/day        300          (Network Reach annotation)
slots per 60s loop         6 static     (Launch wizard)
share of voice bought      1 of 6       (frequency slider = 1)
→ 300 × (1/6) × uptime 99.4% × daypart coverage ≈ 46 impressions/screen/day  ✓
```

**So the core identity is:**

```
impressions = Σ_screens [ footfall_in_selected_dayparts × SOV × uptime × flight_days ]
SOV         = spots_bought_per_loop ÷ total_spots_per_loop
reach       = impressions ÷ avg_frequency        ← the only non-trivial term
cost        = impressions ÷ 1000 × blended_CPM
```

Everything except `avg_frequency` is arithmetic over inventory. That one term is the real modelling
work (§2.3).

### 2.2 The pipeline

```
  Constraints {budget, days, type, objective, creative, dayparts, mix, target_reach?}
        │
        ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ 1  RESOLVE SCOPE                                                │
  │    brand allocation (e.g. AlphaRule: 22,160 screens, 6 cities)  │
  │    × cities × inventory tier × flight window availability       │
  └─────────────────────────────┬───────────────────────────────────┘
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ 2  ELIGIBLE POOL                                                │
  │    per screen: city · tier · footfall/day · daypart profile ·   │
  │    uptime · already-committed SOV (what's left to sell)          │
  └─────────────────────────────┬───────────────────────────────────┘
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ 3  UNIT ECONOMICS  (per screen, per daypart)                    │
  │    slots_per_loop   ← creative length + tier (premium = denser)  │
  │    sellable_spots   ← capacity × (1 − committed SOV)             │
  │    impressions/spot ← footfall_in_daypart × uptime               │
  │    ₹/impression     ← rate card for that tier                    │
  └─────────────────────────────┬───────────────────────────────────┘
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ 4  ALLOCATE            ← the optimisation                       │
  │    mode A (target given): hit reach at minimum cost              │
  │    mode B (blank=maximise): maximise reach within budget         │
  │    v1: GREEDY by reach-per-rupee, then rebalance for city spread │
  └─────────────────────────────┬───────────────────────────────────┘
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ 5  REACH MODEL         ← de-duplication, the hard part           │
  │    frequency curve f(screens, city, flight_days, dayparts)       │
  │    calibrated on person_day                                      │
  │    reach = impressions ÷ f                                       │
  └─────────────────────────────┬───────────────────────────────────┘
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ 6  WALLET CHECK                                                 │
  │    reserve vs available balance → "₹2.50 L from ₹12.30 L"        │
  │    insufficient → plan still returned, flagged, with a shortfall │
  └─────────────────────────────┬───────────────────────────────────┘
                                ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │ 7  PLAN OBJECT   screens · impressions · reach · CPM · burn ·   │
  │                  city allocation · tier mix · headroom · facts[] │
  └──────────┬──────────────────────────────────┬───────────────────┘
             ▼                                  ▼
  ┌────────────────────────┐        ┌──────────────────────────────┐
  │ 8a RULE-BASED ADVICE   │        │ 8b LLM NARRATION             │
  │ "Target met — 107%     │        │ prose, streamed              │
  │  headroom. Spend it by │        │ POST-CHECK: every numeric    │
  │  weighting Evening or  │        │ token ∈ facts[] or the plan  │
  │  adding Premium."      │        │ fails → templated fallback   │
  └────────────────────────┘        └──────────────────────────────┘
```

### 2.3 The reach model is the only real research problem

`reach = impressions ÷ frequency`, and frequency is **not** a constant. It rises with flight length
(the same commuter passes the same screen daily), with screen density in one city, and with narrow
daypart selection. Get it wrong and every plan misstates its headline number.

**You already have the right data.** The v3 pipeline writes a **`person_day`** collection — a
person-grain read model. Reach is a non-additive metric, so it cannot be recovered from the daily
`dashboard_views` rollups; `person_day` is exactly the grain that makes calibration possible.
(`Knowledge.md` §9 and the adgrid-api skill both make this point about non-additive metrics.)

Three levels, ship the first:

| | Model | When |
|---|---|---|
| **v1** | **Lookup table** of `avg_frequency` binned by (city, screens-bought bucket, flight-length bucket, daypart set), fitted from `person_day` over the last 90 days | Ship this |
| v2 | Parametric reach curve — beta-binomial fitted per city; smooth, no bin edges | When bin edges start distorting plans |
| v3 | Per-screen overlap graph from re-identification | Only if v2's error is the binding constraint |

**v1 is a SQL/aggregation job plus a table.** It is honest, explainable ("based on 90 days of
observed footfall in Delhi"), and testable. Do not start at v3.

### 2.4 Validation: back-testing is the gate

A planner that cannot retrodict must not predict.

```
for each completed campaign in the last 6 months:
    feed the planner its original constraints
    compare predicted vs actual: impressions, reach, cost
    report MAPE per metric
```

**Gate: impressions MAPE ≤ 10%, reach MAPE ≤ 20%** before the Calculator ships to users. Reach gets
the looser bound because it depends on the frequency model; if it cannot be met, the plan card shows
reach as a **range**, not a point estimate. Showing "8.3 L" when the model is ±40% is how a planning
tool loses credibility permanently.

Run the back-test in CI on every planner change. It is the planner's equivalent of the eval gate.

### 2.5 Why greedy, not a solver

An LP/MIP would allocate optimally. Start greedy anyway:

- **Explainable.** "We bought Delhi first because it returns the most unique reach per rupee" is a
  sentence. A simplex tableau is not, and Genie has to *explain* its plan.
- **Deterministic and fast.** Same inputs, same plan, every time — which the eval harness needs and
  a user re-running a plan expects.
- **The gap is small.** With ~6 cities × 2 tiers × 4 dayparts the search space is tiny; greedy plus a
  rebalancing pass lands close to optimal.
- Keep it behind a `Allocator` interface so an LP can replace it when back-testing says the gap costs
  real money. (`SCALE.md`'s rule: simple implementation, non-negotiable seam.)

---

## 3. The Advisors — how they recommend

Separate engine, much simpler. Each advisor is a pure function, independently testable and
independently switchable.

```python
def wallet_runway(ctx: UserContext, data: LiveData) -> list[Recommendation]:
    runway_days = data.wallet.balance / data.wallet.burn_per_day
    if runway_days >= THRESHOLD_DAYS:          # config, not literal
        return []
    return [Recommendation(
        advisor_id="wallet_runway",
        severity=critical_if(runway_days < 3),
        title=f"Wallet runway ~{runway_days:.0f} days",
        rationale_facts=[
            Fact("balance",  data.wallet.balance,  source="GET /api/v1/wallet"),
            Fact("burn/day", data.wallet.burn_per_day, source="GET /api/v1/wallet", window="30d"),
        ],
        proposed_action=ActionRef("wallet.request_funds",
                                  prefill={"amount": suggest_topup(data)}),
        confidence="high",
    )]
```

That is literally the deck's *"Wallet runway ~6 days · Burn ₹2.6L/day · balance ₹15.75L → Top up"*.

**v1 set** — each already visible in the designs or directly implied:

`wallet_runway` · `approval_backlog` (oldest-waiting) · `subwallet_short` · `invoice_due` ·
`budget_pacing` · `underdelivery` · `device_health` · `creative_fatigue` · `unassigned_inventory`

**Ranking and restraint.** Score by `severity × confidence × actionability`; show **max 3**; remember
a dismissal for 14 days per `(user, advisor, entity)`. The deck models this well — AdGuide's opener
reports *"Your 8 live campaigns are all pacing to plan — nothing to act on there."* A recommender
that can say "nothing here" is one people keep reading.

**Role-gating is mandatory, not cosmetic** (`FINDINGS.md` §6.7/6.8). An advisor whose
`proposed_action` the user's role cannot perform is either suppressed or re-pointed at someone who
can ("ask your admin to approve"). This is computed from `UserContext.permissions`, never templated.

---

## 4. How Genie embeds into the dashboard

### 4.1 The decision: in-app React component, not an iframe

| | In-app component ✅ | Iframe |
|---|---|---|
| Reading `ui_context` | Direct from router + store | postMessage plumbing on every route change |
| Deep links | `router.push()` | postMessage → parent handler |
| **Prefilling the wizard** | Writes the app's own form state | Cross-origin form seeding — painful |
| Styling | Inherits the design system | Duplicate tokens, drift |
| Isolation | Weaker | Stronger |

Three of the four things Genie must do are DOM- and state-coupled to the dashboard, so the component
wins. Isolation is handled where it matters — Genie's **service** is separate and its **network
calls** are its own.

### 4.2 Integration surface

```
dashboard-web/
  src/
    app.tsx                       <GenieProvider>  ← session, SSE, useGenie()
    genie/
      GeniePanel.tsx              the docked panel (Ask | Calculator tabs)
      useUiContext.ts             subscribes to route + filters, publishes on change
      blocks/                     block renderer → YOUR design system components
        TextBlock · MetricsBlock · ChartBlock · TableBlock
        LinkBlock · SuggestionBlock · ChoiceBlock · PrefillBlock
      actions/
        prefillLaunchWizard.ts    plan → wizard route + seeded form state
        navigate.ts               ontology route → router.push
  packages/
    @adgrid/ontology              ← SHARED. screens, widgets, metric definitions
                                     consumed by BOTH the (i) tooltips AND Genie
```

**`@adgrid/ontology` is the keystone.** The same package that feeds Genie's metric definitions feeds
the **(i)** tooltips already drawn on every metric card. One definition store, two surfaces — so
they cannot drift, and the ontology stops being "Genie overhead" and becomes FE content they
already need.

### 4.3 The `ui_context` contract

Published on every route change, filter change and widget selection:

```jsonc
{
  "ontology_version": "2026.09.1",
  "screen_id": "command_centre",
  "route": "/command-centre",
  "entities": { "client_id": "infinity_adv", "brand_id": null },
  "filters": { "period": "this_month", "date_from": "2026-03-01",
               "date_to": "2026-03-31", "city": null },
  "selection": { "widget_id": "wallet_balance" },
  "visible_metrics": ["wallet_balance", "gross_billing", "gross_profit",
                      "platform_invoice", "active_campaigns"]
}
```

Rendered back to the user as **`● Viewing Home · Overview`**, exactly as the deck draws it. That
line is not decoration — it is the user's only way to see what Genie thinks it is looking at, and
therefore to catch it being wrong.

**`ui_context` is a hint, never an authority.** Every id in it is re-validated against
`UserContext.accessible_client_ids` server-side. A tampered payload must buy nothing.

### 4.4 Auth

No separate login. The panel sends the dashboard's existing JWT; `ag-genie-service` verifies it
against Keycloak JWKS and derives `UserContext` from the token alone. Genie holds **no** service
account, so its blast radius is capped at what the user could already do from the UI.

---

## 5. The whole architecture

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  BROWSER — dashboard-web (React)                                             ║
║                                                                              ║
║   ┌────────────────────────┐        ┌──────────────────────────────────┐    ║
║   │ Dashboard screens      │◀──────▶│ GeniePanel                       │    ║
║   │ Command Centre · Brand │ ui_ctx │  [Ask AdGuide] [Calculator]      │    ║
║   │ Launch wizard · Wallet │        │  ● Viewing Home · Overview       │    ║
║   │                        │◀───────│  blocks · streamed               │    ║
║   │  (i) tooltips ─────────┼───┐    │  ⓘ grounded · advises, you decide│    ║
║   └───────────┬────────────┘   │    └──────────────┬───────────────────┘    ║
║               ▲   prefill blocks · deep links      │                        ║
║               └────────────────┼───────────────────┘                        ║
║                                │                                            ║
║                      @adgrid/ontology  (shared package)                     ║
╚════╤════════════════════════════════════════════════════╤══════════════════╝
     │                                                     │
     │  READS + ALL WRITES                                 │  READS ONLY · SSE
     │  user's own JWT                                     │  user's own JWT
     │                                                     ▼
     │         ╔═══════════════════════════════════════════════════════════╗
     │         ║  ag-genie-service  :9030   (Python / FastAPI)             ║
     │         ║                                                           ║
     │         ║  auth (JWKS) → rate limit → session → resolve ui_context   ║
     │         ║                          │                                ║
     │         ║                  ┌───────▼───────┐  session-first:        ║
     │         ║                  │    ROUTER     │  pending form/confirm  ║
     │         ║                  └───────┬───────┘  before classification ║
     │         ║   ┌─────────┬────────────┼───────────┬──────────┐         ║
     │         ║   ▼         ▼            ▼           ▼          ▼         ║
     │         ║ NAVIGATE  EXPLAIN      DATA       ADVISE       ACT        ║
     │         ║ ontology  ontology   live BFF     PLANNER     prefill     ║
     │         ║ lookup    → RAG        read       ADVISORS    navigate    ║
     │         ║ ~50ms     (gated)    (never       (pure       (NEVER      ║
     │         ║ no LLM               cached)       code)      executes)   ║
     │         ║                          │                                ║
     │         ║        narration (LLM) + numeric post-check               ║
     │         ║             audit · telemetry · cost                      ║
     │         ╚═══╤══════════╤═══════════╤══════════════════════╤═════════╝
     │             ▼          ▼           ▼                      │
     │        ┌─────────┐ ┌──────────┐ ┌──────────────┐          │ READS
     │        │  Redis  │ │ Postgres │ │ LLM + embed  │          │  only
     │        │ cache + │ │ pgvector │ │  providers   │          │
     │        │ session │ │ audit    │ │  (per task)  │          │
     │        │         │ │ freq     │ │              │          │
     │        └─────────┘ └──────────┘ └──────────────┘          │
     │                                                           │
     └──────────────────────────┬────────────────────────────────┘
       READS + ALL WRITES       ▼
                   ╔════════════════════════════════════╗
                   ║  ag-dashboard-bff       :9021      ║
                   ║  JWT forwarded verbatim            ║
                   ╚═════════════════╤══════════════════╝
                                     ▼
                   ╔════════════════════════════════════╗
                   ║  ag-open-service        :9022      ║
                   ║  ★ THE AUTHORIZATION GATE ★        ║
                   ║  @RequireAccess · @RoleRequired    ║
                   ║  @EntityOwnershipRequired          ║
                   ╚═════════════════╤══════════════════╝
                                     ▼
            analytics · campaign · billing · user · idam · otp
                                     ▼
                          MongoDB · Postgres
```

**Read the star.** Genie's permission checks are advisory and exist only to avoid starting a form
the user cannot finish. The single enforcement point is `@RequireAccess` in `ag-open-service`,
reached with the user's own token, exactly as when they click the button themselves.

---

## 5.1 Read path vs write path — Genie never writes

The browser holds **two independent connections**: one to the BFF (as today), one to Genie. They do
not depend on each other — Genie being down leaves the dashboard untouched, and a closed panel costs
nothing.

The asymmetry that matters: **Genie calls the BFF for reads only. Every write is browser → BFF.**

```
READ PATH — Genie answering "how is this campaign doing?"

  browser ──▶ ag-genie-service ──▶ BFF ──▶ open-service ──▶ domain
             (user's JWT)      (same JWT forwarded)


WRITE PATH — creating the campaign Genie planned

  browser ──▶ ag-genie-service
     │              │
     │◀─────────────┘   returns a BLOCK — data, not a call:
     │                  { capability: "prefill.launch_wizard",
     │                    values: { screens: 1818, days: 30, budget: 250000, … } }
     ▼
  browser: router.push('/campaigns/new') + seed form state
     │
     ▼
  HUMAN edits, clicks "Submit for approval"
     │
     ▼
  browser ──▶ BFF ──▶ open-service ──▶ campaign-service
     │            ▲
     │            └── the DASHBOARD makes this call. Genie is not in this path.
     ▼
  Approvals → InitiateConfirmCampaign → OTP to admin's mobile
            + ToS acknowledgement → CompleteConfirmCampaign
```

**Genie hands the browser a filled-in form, not an instruction to the backend.** The dashboard's own
existing code performs the mutation — the same code path as if the user had typed every field.

Three consequences, all of them the point:

- **Genie has no write surface to attack.** Prompt injection can at most produce a wrong prefill,
  which a human reviews in an editable form before submitting.
- **The wizard's validation, the approval queue and the OTP gate all apply automatically**, because
  nothing bypassed them.
- **The audit trail records the user** creating the campaign, not a bot acting on their behalf.

### The one real cost of two paths: numbers drifting

The screen fetched at time *T*; Genie fetches at *T+20s*. They can disagree, and the user is looking
at both. Mitigations, in order:

1. `ui_context` carries the **request parameters** the screen used (filters, window), so Genie issues
   an identical query rather than a similar one.
2. Genie always echoes the resolved window — *"For 1–31 March…"*.
3. **`ui_context` carries identifiers and filters only, never values.** It is tempting to have the FE
   pass the numbers it already fetched and skip the round-trip. Don't: client-supplied values are a
   tampering surface, and it would break provenance — Genie could no longer say which API a figure
   came from.

The duplicate read is one extra call per Genie *turn*, not per page render.

---

## 6. Runtime flow: "plan a 2.5 lakh campaign for Coca-Cola"

```
 user types in the panel
   │
   ▼
 JWT verified → UserContext{AD_PARTNER_ADMIN, infinity_adv, brands[…], perms[…]}
   │
   ▼
 session-first router: no pending form → classify → ADVISE/PLAN
   │
   ▼
 LLM parse → constraints{budget: 250000, brand: "coca_cola", days: ?, type: ?}
   │         schema-validated · identity fields rejected if present
   ▼
 missing required slot (days) ──▶ ask ONE question, persist form, return
   │                              (or: Calculator tab already has them)
   ▼
 PLANNER (pure code, §2.2)
   ├─ scope: Coca-Cola's allocation ∩ accessible_client_ids   ← re-validated
   ├─ pool → unit economics → allocate → reach → wallet check
   └─ Plan{1818 screens, 24.8L imp, 8.3L reach, ₹101 CPM, facts[…]}
   │
   ▼
 narrate (LLM) ─── numeric post-check: every figure ∈ facts[] ── fail ▶ templated
   │
   ▼
 blocks: [metrics] [table: city allocation] [suggestion: headroom]
         [prefill: capability=prefill.launch_wizard]
   │
   ▼
 user clicks "Prefill wizard"
   │
   ▼
 FE: router.push('/brands/coca_cola/campaigns/new') + seed form state
     banner: "Prefilled by AdGuide — everything stays editable"
   │
   ▼
 HUMAN edits and clicks "Submit for approval"        ← gate 1
   │
   ▼
 Approvals queue → admin selects → OTP to registered mobile
                 + ToS acknowledgement checkbox      ← gate 2 (Genie cannot pass)
   │
   ▼
 campaign live · audit row written
```

Genie touches nothing after the prefill. **Three gates — human edit, human submit, OTP-verified
human approval — and Genie can pass none of them.** That is the safety story in one line, and it is
the deck's design, not an addition to it.

---

## 7. Build order implied by this architecture

| | Component | Depends on | Note |
|---|---|---|---|
| 1 | `@adgrid/ontology` | `FINDINGS.md` §6 rulings | Blocks everything; also ships the (i) tooltips |
| 2 | `GeniePanel` + `ui_context` | ontology | Can render stub blocks immediately |
| 3 | NAVIGATE + EXPLAIN | ontology | First demoable value, no LLM on the hot path for NAVIGATE |
| 4 | DATA path | BFF reality audit (`FINDINGS.md` §7) | **Blocked until we know what exists** |
| 5 | **Frequency curve** from `person_day` | analytics access | Start early — it is a data job, parallel to everything |
| 6 | **PLANNER** | 5 + inventory + rate cards | The long pole |
| 7 | Back-test harness | 6 + completed campaigns | Gates the Calculator's release |
| 8 | ADVISORS | 4 | Parallel to 6; pure functions over BFF data |
| 9 | Prefill bridge | 6 + wizard variants | Small, once the plan object is stable |

**Items 5 and 6 are the critical path and neither involves an LLM.** Start the frequency-curve job
as soon as someone can query `person_day`; it needs no product decisions and it is the input the
Planner cannot be built without.

> Superseded by `BUILD.md`, which sequences this as Stages 0–11 with exit criteria.

---

## 8. Access model — what Genie can and cannot reach

The one-line version: **Genie can see whatever you can see, and can do nothing you haven't clicked.**

### 8.1 The rule

**Not GET vs POST. Does it change state?**

| Call | Method | Genie | Why |
|---|---|:--:|---|
| `POST /api/v1/campaigns/simulate-cost` | POST | ✅ | Computes a price, changes nothing |
| `POST /api/v1/campaigns/availability-search` | POST | ✅ | A query with a large body |
| `GET /api/v1/clients/{id}/network` | GET | ✅ | Read |
| `POST /api/v1/campaigns` | POST | ❌ | Creates a campaign |
| `POST /api/v1/wallet/topup` | POST | ❌ | Moves money |
| `InitiateConfirmCampaign` | POST | ❌ | **Sends an SMS.** Side effects outside the system are writes |

That last row is the one that gets misclassified. A call that looks like a read but triggers an
email, an SMS, a webhook or a queue message is a write.

### 8.2 Inventory

**Genie has:**

| | Detail |
|---|---|
| **Read APIs via the BFF** | Carrying the **user's own JWT**. Scoped by `ag-open-service` to their agency and its brands — exactly what that user could already see |
| **Its own Postgres** | Ontology cache, KB vectors, capability vectors, frequency lookup table, audit log, eval runs, session mirror |
| **Its own Redis** | Cache layers L0–L4, live session state |
| **`UserContext`** | From the verified token only: user id, role, client id, accessible client ids, permissions, timezone, locale |
| **`ui_context`** | From the screen. **Untrusted hints** — every id re-validated server-side |
| **Conversation history** | Its own sessions, scoped to one user |

**Genie does not have:**

- ❌ Any service account or credential of its own
- ❌ Direct database access to Adgrid data — no Mongo, no domain-service Postgres
- ❌ Direct gRPC to domain services (they carry **no security config** and trust open-service)
- ❌ Any write endpoint, including for its own tenant
- ❌ Other tenants' data — enforced at open-service, not by Genie's good behaviour
- ❌ The `Hm*` / `Rm*` / `Se*` / `Kw*` / `/v1/agent/**` surfaces — not this dashboard
- ❌ The ability to complete an OTP

### 8.3 Why this survives a bad day

Because Genie holds no elevated credential, the worst outcome of a fully prompt-injected Genie is a
call the user could have made themselves from the UI. Every mitigation above it — preflight checks,
scope validation, delimited untrusted content — is defence in depth. **This property is the actual
security control.**

---

## 9. Data requirements register

Everything Genie needs, by consumer. Fill in `endpoint` — the BFF path — and `status`.

Legend: **✓ verified present** (I confirmed the path in the BFF source) · **? assumed** (exists in a
domain service; BFF exposure unconfirmed) · **✗ gap** (needs building)

### 9.1 Identity and context — needed by Stage 2

| # | What Genie needs | Why | Endpoint | Status |
|---|---|---|---|:--:|
| I1 | Keycloak **JWKS URL** | Verify the JWT, derive `UserContext` | `________` | ? |
| I2 | Current user profile — role, client id, permissions | Everything role-gated: preflight, advisor suppression, the opener | `________` (`MeController`?) | ? |
| I3 | Client hierarchy — brands under this agency | Tenancy: `accessible_client_ids` | `________` | ? |
| I4 | Test users, one per role, with obtainable JWTs | All testing | — | ✗ |

### 9.2 Planner — needed by Stage 6

| # | What Genie needs | Why | Endpoint | Status |
|---|---|---|---|:--:|
| P1 | Screen inventory for a brand | The eligible pool | `GET /api/v1/clients/{clientId}/network` | ✓ |
| P2 | Inventory by city | City allocation | `GET /api/v1/clients/{clientId}/network/by-city` | ✓ |
| P3 | Availability in a flight window | What can actually be bought | `POST /api/v1/campaigns/availability-search` | ✓ |
| P4 | Earliest start date | "Start in 3 days" | `POST /api/v1/campaigns/start-date-availability` | ✓ |
| P5 | **Cost simulation** | Pricing — **do not reimplement** | `POST /api/v1/campaigns/simulate-cost` | ✓ |
| P6 | Wallet balance + coverage | "Reserves ₹2.50 L from ₹12.30 L" | `________` | ? |
| P7 | Footfall / audience per city or screen | Reach inputs | `________` | ? |
| P8 | Past campaigns of this client, with outcomes | `campaign_setup`: "your last 5 comparable campaigns" | `GET /api/v1/clients/{clientId}/campaigns` | ✓ |
| P9 | **Frequency curve** | Reach de-duplication | Genie's own Postgres, from `person_day` | ✗ build |

**Open question on P5** (`SERVICES.md` §8): does `simulate-cost` price a *hypothetical* selection, or
must real device ids come from P3 first? If the latter, the allocator needs a local pricing
approximation for its search and one authoritative P5 call for the final plan. **This changes how
Stage 6 is built**, so it is a Stage 0 answer.

### 9.3 Advisors — needed by Stage 9

| # | Advisor | Needs | Endpoint | Status |
|---|---|---|---|:--:|
| A1 | `wallet_runway` | Balance + burn rate (30d) | `________` | ? |
| A2 | `approval_backlog` | Pending approvals with age, oldest first | `________` | ? |
| A3 | `subwallet_short` | Sub-wallet balances vs committed spend | `________` | ? |
| A4 | `invoice_due` | Platform invoice amount + due date | `________` | ? |
| A5 | `budget_pacing` | Per-campaign spend to date vs budget vs elapsed days | `________` | ? |
| A6 | `underdelivery` | Per-campaign impressions delivered vs planned, **daily** | `________` | ? |
| A7 | `device_health` | Uptime / presence for devices carrying active campaigns | `________` | ? |
| A8 | `creative_fatigue` | Per-creative engagement over time | `________` | ? |
| A9 | `unassigned_inventory` | Available unassigned screens / batches | `________` | ? |

A6 needs a **daily series**, not a window total — pacing cannot be detected from a single aggregate.
Flag early if only totals are available.

### 9.4 DATA path — needed by Stage 4, one row per screen

| # | Screen | Endpoint(s) backing its widgets | Status |
|---|---|---|:--:|
| D1 | Command Centre | `________` (likely several — the most fan-out-heavy screen) | ? |
| D2 | Notifications & Approvals | `________` | ? |
| D3 | Brand Management | `________` | ? |
| D4 | Creative Library | `________` | ? |
| D5 | Launch Campaign | P3, P4, P5 above | ✓ |
| D6 | Inventory Management | P1, P2 above | ✓ |
| D7 | Wallet Management | `________` | ? |
| D8 | Team Management | `________` | ? |
| D9 | Brand Dashboard (CM) | `________` | ? |

**These are recorded per metric, not per screen** — each ontology metric entry carries its own
`source.api`. The table above is the checklist; the ontology is where the answer lives.

### 9.5 Not endpoints — but still blocking

| # | What | Needed by | Status |
|---|---|---|:--:|
| N1 | The six metric rulings (`FINDINGS.md` §9) | Stage 1 | ✗ |
| N2 | Authoritative loop length from `BillingCostCell` | Stage 1, 6 | ✗ |
| N3 | Which cost leg + pre/post-GST the plan card shows (`SERVICES.md` §6) | Stage 6 | ✗ |
| N4 | Query access to `person_day` in QA | Stage 5 | ✗ |
| N5 | Seeded adversarial QA tenant (`BUILD.md` §5.4) | Stage 4 | ✗ |
| N6 | LLM + embedding provider and region | Stage 2 | ✗ |
| N7 | Tier A corpus author (`KNOWLEDGE.md` §8) | Stage 10 | ✗ |

### 9.6 For each endpoint you hand over, include

Not just the path — these four save a round of rework each:

1. **Full path + method**, and whether it is on the BFF or only on open-service (if only open-service,
   it needs a BFF passthrough — Genie does not reach around).
2. **The auth gate** — `@RequireAccess` with which `Permission`, or `@RoleRequired` with which roles.
   This populates the capability catalog's `authorization` block and the advisor role-gating.
3. **A real sample response** from the seeded tenant. Not the schema — the actual JSON. Field names
   verified against real documents is a rule we already pay for elsewhere.
4. **Typical and p95 latency.** The Planner may call `simulate-cost` repeatedly; the DATA path has a
   1.5 s budget. Latency changes the design, not just the tuning.
