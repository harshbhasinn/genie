# BFF → Open → Domain — the service map Genie calls through

Measured from `C:\Work\Adgrid\AWS\Java_Backend\production` on 2026-09-17.
Companion to `ARCHITECTURE.md`. **Supersedes `FINDINGS.md` §7** — see §6.

---

## 1. The three layers

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  dashboard-web  +  GeniePanel                                                 ║
║  one call per screen · Authorization: Bearer <JWT>                            ║
╚═══════════════════════════════╤═══════════════════════════════════════════════╝
                                │ HTTPS / JSON
        ┌───────────────────────┴────────────────────────┐
        ▼                                                ▼
╔═══════════════════════════╗                ╔═══════════════════════════════════╗
║ ag-dashboard-bff   :9021  ║                ║ ag-genie-service   :9030          ║
║                           ║                ║                                   ║
║ "what the screen needs"   ║                ║ "what the sentence needs"         ║
║                           ║                ║                                   ║
║ 44 controllers            ║                ║ router · planner · advisors       ║
║ 434 endpoints             ║                ║ ontology · RAG · sessions         ║
║ 39 Feign clients          ║                ║                                   ║
║   ALL → one URL:          ║                ║ NO service account.               ║
║   OPEN_SERVICE_URL        ║◀───────────────╢ Forwards the user's own JWT.      ║
║                           ║  same JWT      ║                                   ║
║ Composes · shapes · paginates              ║ Calls the BFF so its numbers      ║
║ NO business rules         ║                ║ match the screen exactly.         ║
║ NO database               ║                ║                                   ║
╚═══════════════╤═══════════╝                ╚═══════════════════════════════════╝
                │ Feign (REST/JSON), Authorization header forwarded
                ▼
╔═══════════════════════════════════════════════════════════════════════════════╗
║ ag-open-service   :9022          ★ THE AUTHORIZATION GATE ★                   ║
║                                                                               ║
║ 54 controllers · 299 endpoints · 32 gRPC clients → 9 domain services          ║
║                                                                               ║
║ Owns:  the public REST contract /v1/open/**                                   ║
║        JWT validation · role checks · per-client permission checks            ║
║        fan-out (one REST call → several gRPC calls)                           ║
║        protobuf ⇄ JSON translation                                            ║
║ Owns no business logic and no database.                                       ║
║                                                                               ║
║ Filter + interceptor chain:                                                   ║
║   ApiRequestFilter → RoleAuthInterceptor → RequestInterceptor                 ║
║   ScopeCacheClearFilter · StationAuthInterceptor · WebhookAuthInterceptor     ║
║ Authorization aspects (4):                                                    ║
║   AccessControlAspect      ← @RequireAccess(clientIdExpression, permission)   ║
║   EntityOwnershipAspect    ← @EntityOwnershipRequired                         ║
║   ClientStatusGuardAspect  ← @RequireClientOperational                        ║
║   (+ @RoleRequired on the interceptor path)                                   ║
╚═══════════════════════════════╤═══════════════════════════════════════════════╝
                                │ gRPC (protobuf / HTTP-2)
    ┌──────────┬────────────┬───┴────────┬───────────┬──────────┬──────────┐
    ▼          ▼            ▼            ▼           ▼          ▼          ▼
 analytics  campaign     billing      user        idam       otp     hardware-mgmt
 :9027/:9097  :9524       :9526      :9090       :9093     :9092     + telemetry
                                                                     + retail-ops
    │           │            │           │
    ▼           ▼            ▼           ▼
 MongoDB     Postgres     Postgres    Postgres  ← adgrid_analytics is written by
 adgrid_                                          the batch pipeline, NEVER by the API
 analytics
```

### The analytics data is pre-computed, and that constrains Genie

Not our infrastructure — pre-existing, and it produces every CAMERA-badged metric on the Command
Centre (Network Reach, Avg Attention, Avg Dwell, Verified Impressions, Audience Profile).

```
device .json.gz → S3 incoming/video/
  T3  ingest       → raw/video/**.parquet        per-file, EVENT-triggered
  T5  re-identify  → identity/video/…        ┐
  T6  derive facts → facts/video/…           ├ one Lambda, SCHEDULED
  T7  rollup       → MongoDB adgrid_analytics┘
```

`Knowledge.md` §9: *"The API **never** computes analytics on demand. A batch pre-computes
query-shaped documents; the API reads and sums them."* Unique visitors over an arbitrary window is
not a request-time query over tens of millions of face detections; pre-aggregating turns a 30-second
scan into a 20 ms indexed read.

**Three consequences for Genie:**

1. **Freshness is ~6 hours, not real-time.** Genie must never imply "now" about a camera metric.
   Every camera-derived number carries an **`as_of`** stamp in its provenance alongside `source_api`.
   "How many people saw my ad in the last hour?" has no answer and Genie must say so.
2. **`person_day` comes from here** — the non-additive, person-grain collection that feeds the
   frequency curve and therefore the Planner's reach model. Stage 5 is downstream of this pipeline.
3. **The batch is a full recompute every run.** Self-healing, but numbers can change *retroactively*.
   A figure Genie quoted yesterday may legitimately differ today, so the audit log records `as_of`,
   not just the value.

### The scoping rule Genie must not get wrong

Two different tenancy keys, often on the same screen:

| collection | grain | scoped by | holds |
|---|---|---|---|
| `dashboard_views` | (owner_id, store_id, date) | **owner_id** | footfall, demographics, rhythm, dwell sums |
| `content_views` | (client_id, store_id, creative_id, date) | **client_id** | impressions, views, reach, cohort×zone |
| `person_day` | (person_id, date, owner_id, store_id, device_id) | **owner_id** | visits, dwell — the non-additive source |

- **audience** scopes by `owner_id` — everyone who entered *your stores*, whoever's creative played
- **content** scopes by `client_id` — only *your creatives*, wherever they played

Measured on seed data: Zudio has **210** content docs of its own while **462** played on its screens
— 252 correctly invisible to it. **Resolve the wrong key and Genie reports another party's data.**
This belongs in the ontology per metric, not as a general rule.

### Open question — possibly a ninth contradiction

`dashboard_views` is scoped by `owner_id`, but `ClientType.AD_PARTNER` has `ownsStores = false`. Yet
the Command Centre shows an AD_PARTNER a full audience panel — Network Reach, Audience Profile,
Location Distribution. Either those are served by a different aggregation than the one documented
here, or the scoping does not line up. This is a cross-tenant question and needs an answer from
whoever owns the rollup before those metrics enter the ontology.

**One fact worth knowing:** the BFF's 39 Feign clients have 39 different logical names
(`campaign-open-service`, `wallet-open-service`, `brand-open-service`…) but every one resolves to
the same `${OPEN_SERVICE_URL}`. They are naming seams for a future split, not separate services today.

---

## 2. What each layer is for

| Layer | Job | Allowed to | Not allowed to |
|---|---|---|---|
| **BFF** :9021 | Shape data for **one frontend**. One screen, one call. | Compose calls, rename fields, set pagination defaults, merge two backend calls into one card | Hold business rules · touch a database · make authorization decisions |
| **open-service** :9022 | Be the single externally-reachable HTTP surface, and **decide who may see what** | Validate JWTs, check roles/permissions/ownership, fan out over gRPC, translate protobuf ⇄ JSON | Hold business logic · own a database |
| **domain services** | Own a bounded context and its data | Everything about their domain | Assume an unauthenticated caller — they have no security config, because open-service already decided |

That last line is why Genie must never call a domain service directly. `ag-analytics-service` has
**no security config at all**; it trusts that open-service already authorized the caller.

---

## 3. The nine domain services

| Service | gRPC clients in open | Owns | Key RPCs for Genie |
|---|:--:|---|---|
| **campaign-service** :9524 | 8 | Campaigns, creatives, batches, device content, deployment plans, programs, fallback creatives | `GetClientNetworkInventory` · `GetNetworkByCity` · `GetAvailableDevicesForCampaign` · `GetStartDateAvailability` · `CreateCampaign` · **`InitiateConfirmCampaign` / `CompleteConfirmCampaign`** · `PauseCampaign` / `ResumeCampaign` · `GetClientDashboardStats` · `GetBrandAnalytics` |
| **billing-service** :9526 | 5 | Wallets, rate cards (batch/client/premium), surcharges, settlement, refunds | **`SimulateCampaignCost`** · `GetWalletBalance(s)` · `CheckWalletCoverage` · `TopUpWallet` · `GetWalletKpis` · `GetWalletStatement` · `GetCampaignConsumption` · `GetPlatformRevenueSummary` · `MarkMonthSettlementPaid` |
| **analytics-service** :9027 / :9097 | 9 | Camera-measured audience & content analytics, audit log, device presence, revenue breakdown, store overview, voice | `GetAudienceWindow` · `GetContentWindow` · `GetContentPerformance` · `GetCreativeLeaderboard` · device presence · revenue breakdown |
| **user-service** :9090 | 5 | Clients (the tenancy spine), access grants, contracts, stores, users | `ListClients` · `GetClientDetail` · `ListChildClients` · access grants |
| **idam-service** :9093 | 1 | Identity & access management, Keycloak integration | — |
| **otp-service** :9092 | 1 | OTP issue/verify | **the approval gate** |
| **hardware-mgmt** | 1 | Rig stations, consignments, device QC, fingerprints | — (out of Genie scope) |
| **telemetry-service** | 1 | Device telemetry | uptime / proof-of-play |
| **retail-operations-service** | 1 | Retail leads, projects, field ops | — (out of Genie scope) |

**Scope note.** Roughly a third of the open-service surface — `Hm*` (hardware), `Rm*` (retail
management), `Se*` (sales engineering), `Kw*` (Kelly workers), `/v1/agent/**` (station agents, which
uses station-bearer creds and is *explicitly skipped* by the JWT interceptor) — is **not** part of the
Agency Admin Dashboard. Genie's capability catalog must exclude it outright, not merely
deprioritise it.

---

## 4. Genie's call map, by intent

```
GeniePanel
    │
    ├── NAVIGATE ──────▶ ontology only. No network call. ~50 ms.
    │
    ├── EXPLAIN ───────▶ ontology → (fallback) pgvector. No BFF call.
    │
    ├── DATA ──────────▶ ag-dashboard-bff ──▶ ag-open-service ──▶ domain
    │                    always live · never cached · numbers must match the screen
    │
    ├── ADVISE ────────▶ PLANNER  → inventory + SimulateCampaignCost + reach model
    │                    ADVISORS → wallet · campaigns · devices · approvals
    │
    └── ACT ───────────▶ prefill / navigate only. Genie never executes.
```

### Screen → service, for the screens in the deck

| Dashboard screen | Primary domain source | Notable |
|---|---|---|
| Command Centre | analytics (audience, content) + billing (wallet KPIs, revenue) + campaign (dashboard stats) | The most fan-out-heavy screen; a strong BFF-composition candidate |
| Notifications & Approvals | campaign (`InitiateConfirmCampaign`/`CompleteConfirmCampaign`) + **otp-service** | The OTP gate lives here |
| Brand Management | user-service (`ListChildClients`, `GetClientDetail`) + campaign (`GetBrandAnalytics`) | `AD_PARTNER` → `BRAND_UNDER_AD_PARTNER` |
| Creative Library | campaign (`creative_library_service`) + analytics (`creative_library_analytics`) | |
| Launch Campaign | campaign (`GetAvailableDevicesForCampaign`, `GetStartDateAvailability`, `CreateCampaign`) + billing (`SimulateCampaignCost`, `CheckWalletCoverage`) | What Genie prefills |
| Inventory Management | campaign (`GetClientNetworkInventory`, `GetNetworkByCity`) + telemetry | The Planner's pool |
| Wallet Management | billing (`GetWalletBalance(s)`, `GetWalletStatement`, `TopUpWallet`, `GetWalletKpis`) | Two-tier wallet |
| Team Management | user-service (access grants, members) | Admin-only |

---

## 5. The authorization chain, precisely

A Genie-initiated read travels the same path as a button click:

```
GeniePanel
  └─ Authorization: Bearer <user JWT>          ← Genie adds nothing of its own
      │
      ▼
ag-genie-service
  ├─ verify JWT against Keycloak JWKS → UserContext
  ├─ PREFLIGHT  permissions vs capability.authorization   ← ADVISORY ONLY (UX)
  ├─ SCOPE      entity ids ∈ accessible_client_ids        ← ADVISORY ONLY (UX)
  └─ forward the SAME token to the BFF
      │
      ▼
ag-dashboard-bff → Feign, Authorization header forwarded verbatim
      │
      ▼
ag-open-service                                ★ ENFORCEMENT ★
  ApiRequestFilter        request id, MDC, logging
  RoleAuthInterceptor     JWT validity + @RoleRequired
  AccessControlAspect     @RequireAccess(clientIdExpression, permission)
  EntityOwnershipAspect   @EntityOwnershipRequired — the entity belongs to this tenant
  ClientStatusGuardAspect @RequireClientOperational — client is not suspended
      │
      ▼
domain service (no security config — trusts the gate above)
```

**Genie's checks are advisory; open-service's are authoritative.** Because Genie carries no elevated
credential, a fully prompt-injected Genie can at worst call an endpoint the user could already reach
from the UI.

**Preflight-pass + backend-403 is a bug, not a transient.** It means the capability catalog has
drifted from the controller. Refuse, audit, **alert** — never retry.

---

## 6. Correction to `FINDINGS.md` §7 — the backend gap is much smaller than I thought

I judged the backend gap from controller names (`HmStation`, `RmDevice`, `SeLead`) and concluded the
deck and the BFF might describe different products. Reading the protos shows otherwise: the
agency-facing functionality is largely built, it just lives in `campaign-service` and
`billing-service` rather than in obviously-named controllers.

**Most consequential: `SimulateCampaignCost` already exists**, and it is more capable than the model
I derived in `ARCHITECTURE.md` §2:

```protobuf
SimulateCampaignCostRequest {
  client_id · brand_id · role · start_date · end_date
  creative_layout · image_slots_requested · video_slots_requested
  repeated BillingCostCell cells   // (batch ∩ combo ∩ timeSlot) with per-cell loops
}                                  //  — prices BOTH legs

SimulateCampaignCostResponse {
  adgrid_to_ad_partner_total_cost + breakdown     // leg 1
  ad_partner_to_brand_total_cost  + breakdown     // leg 2
  total_days · total_slots_per_day
  city_cost_summary · device_type_cost_summary · shop_type_cost_summary
  base_cost · gst_percentage · gst_amount · total_cost_with_gst
}
```

### What this changes for the Planner

| Planner stage (`ARCHITECTURE.md` §2.2) | Status |
|---|---|
| 1–2 Resolve scope · eligible pool | **Exists** — `GetClientNetworkInventory`, `GetNetworkByCity`, `GetAvailableDevicesForCampaign`, `GetStartDateAvailability` |
| 3 Unit economics / pricing | **Exists** — `SimulateCampaignCost`, and it is loop-aware per cell |
| 4 **Allocate** (choose the cells) | **Gap — build this** |
| 5 **Reach model** (de-duplication) | **Gap — build this** |
| 6 Wallet check | **Exists** — `CheckWalletCoverage`, `GetWalletBalance` |
| 7–8 Plan object · advice · narration | Build (thin) |

**The Planner is no longer "build a media-planning engine."** It is: *choose the cells, model the
reach, and call `SimulateCampaignCost` to price them.* That is a substantially smaller and
better-defined job, and it removes the largest schedule risk in `PLAN.md`.

`BillingCostCell` is the unit of allocation — `(batch ∩ combo ∩ timeSlot)` with per-cell loops. The
allocator's output is a list of these; the greedy heuristic in `ARCHITECTURE.md` §2.5 ranks
candidate cells by reach-per-rupee, and pricing comes back from billing rather than from our own
rate-card arithmetic. **Do not reimplement pricing.**

### Three new questions this raises

1. **Which leg does AdGuide's plan card show?** `SimulateCampaignCost` returns
   `adgrid_to_ad_partner_total_cost` *and* `ad_partner_to_brand_total_cost`. The deck's
   "Campaign cost ₹2.50 L" is one of them, and the difference is the agency's margin — the same
   margin the Command Centre reports as Gross Profit 56.9%.
2. **Is the plan's cost pre- or post-GST?** The response carries both (`base_cost` vs
   `total_cost_with_gst`, 18%). The wallet is debited in one of them. A plan that quotes pre-GST and
   reserves post-GST is off by 18% at the worst possible moment.
3. **`FINDINGS.md` §6.3 (60s vs 120s loop) has an authoritative answer** — the per-cell loop values
   in `BillingCostCell`. Read it from the data rather than asking the design team, then make the
   annotation match.

---

## 7. Where Genie's own endpoints sit

Genie exposes its own small REST surface, consumed only by `GeniePanel`:

| Endpoint | Purpose |
|---|---|
| `POST /v1/genie/chat` (SSE) | The conversation turn. Body carries `message`, `session_id`, `ui_context` |
| `GET  /v1/genie/suggestions` | Proactive advisors for a screen. Called on panel open, async, never blocking |
| `POST /v1/genie/plan` | Calculator tab. Constraints in, `Plan` out. No LLM on this path |
| `GET  /v1/genie/ontology` | Ontology version + screen/metric definitions, for the FE's (i) tooltips |
| `GET  /healthz` · `/readyz` · `/metrics` | Standard |

**`/v1/genie/plan` has no LLM in it.** The Calculator tab is a deterministic form calling a
deterministic engine; that is what lets it be back-tested, and what keeps it working when the LLM
provider is down.

---

## 8. Open questions for the backend team

1. Are `ag-campaign-service` and `ag-billing-service` deployed and reachable in QA? They are not
   cloned in `Java_Batch/production/`, so I have read their contracts but not their implementations.
2. Does `SimulateCampaignCost` price a *hypothetical* selection, or does it require real device ids
   from `GetAvailableDevicesForCampaign` first? This decides whether the Planner can iterate cheaply
   over candidate allocations or must resolve devices on every trial.
3. What is `SimulateCampaignCost`'s latency? The allocator may call it tens of times per plan. If it
   is slow, the allocator needs a local pricing approximation for the search and one authoritative
   call for the final plan.
4. Is there an existing reach/frequency model anywhere, or is `person_day` the only source?
5. ~~Confirm: is `InitiateConfirmCampaign` / `CompleteConfirmCampaign` the OTP two-step?~~
   **Answered in `S0-ANSWERS.md` B4 — no.** Those two are a *reserve-then-confirm* pair with a TTL
   and carry no OTP field. The OTP is orchestrated one layer up in `ag-open-service`
   (`CampaignOpenServiceImpl` uses both `CampaignGrpcClient` and `OtpGrpcClient`).

   **And it is a platform pattern, not a campaign special case.** `OtpGrpcClient` is used by nine
   open-service implementations — campaign, wallet, batch, contract, deployment plan, promo banner,
   client admin, member, client — behind a generic `InitiateOtpActionResponse`. That is the
   definitive list of operations Genie can never complete, and it is broader than the deck showed.
