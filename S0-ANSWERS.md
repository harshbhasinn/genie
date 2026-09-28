# S0 — answers log

Live record of Stage 0. Every item is either **ANSWERED** (with evidence) or **OPEN** (with who owns it).
Started 2026-09-18.

**Status after round 2 of transcription:**

| | Count |
|---|---|
| Answered from the contracts, no meeting needed | **4** (B1, B2, B3, B4) |
| Rulings dissolved by reading the data model | **1** (R3) |
| Rulings open | **11** (R1, R2, R4, R5, R6, R7, **R8–R12 new**) |
| Access items open | 4 (C1–C4) |
| Your decisions open | 4 (D1–D4) |
| New technical questions raised | 4 (N8–N11) |

⚠️ **The ruling count grew from 6 to 11 after transcribing one additional screen.** Reconciling the
design's numbers is a workstream, not a single decision round — see "What this changes" at the end.

---

## B — Backend lookups

### B1 — Does `simulate-cost` price a hypothetical selection? ✅ ANSWERED — YES, at cell granularity

`BillingCostCell` carries **`int64 device_count`**, not a list of device ids:

```protobuf
message BillingCostCell {
  string batch_id = 1;
  DeviceType device_type = 2;
  string city = 3;
  ShopType shop_type = 4;
  TimeSlots time_slot = 5;
  int64 device_count = 6;          // ← a COUNT, not device ids
  int32 loops_per_time_slot = 7;
  ...
  string store_id = 13;
  string owner_client_id = 14;
  bool   is_premium = 15;
  repeated BillingOwnershipLot ownership_lots = 16;
}
```

**What this means for the allocator (S6):**

The allocator works at **cell granularity** — `(batch × device_type × city × shop_type × time_slot)`
with a device *count*. It does **not** need to resolve individual devices to get a price. So the
loop is:

```
1× availability-search   → the eligible cells, with their batch_id / store_id /
                           owner_client_id / is_premium / ownership_lots / loops_per_time_slot
N× simulate-cost         → price different compositions of those cells (vary device_count)
1× simulate-cost         → the final authoritative price for the chosen composition
```

**This is the good outcome.** The allocator can iterate cheaply without a local pricing
approximation, provided `simulate-cost` latency is acceptable. **Still open: the p95 latency.**

⚠️ The cells must carry *real* `batch_id`, `store_id`, `owner_client_id` and `ownership_lots` — those
come from `availability-search`. The allocator may vary `device_count` freely; it may **not** invent
a cell.

⚠️ `ownership_lots` has explicit precedence: *"when this is non-empty it WINS over
`resolved_adgrid_*_amount`"*, and *"Non-empty with no rate row covering a priced day = a hard error"*.
The allocator must pass ownership lots through untouched.

---

### B2 — Which cost leg? ✅ ANSWERED — the wallet is debited on the **AdGrid → AdPartner** leg

`InitiateConfirmCampaignRequest`:

```protobuf
string adgrid_cost = 5;   // Adgrid→AP base (ex-GST) persisted for the wallet debit (C5)
```

So for the plan card's **"Reserves ₹2.50 L from ₹12.30 L"**, the reservation figure is
`adgrid_to_ad_partner_total_cost`. That is what actually leaves the wallet. Settled.

**Still a product decision:** whether the headline *"Campaign cost"* on the plan card shows the same
AdGrid→AP figure or the brand-facing `ad_partner_to_brand_total_cost`. The difference is the agency's
margin. My recommendation: show the AdGrid→AP figure, because it is the one that matches the wallet
reservation directly beneath it — showing two different bases in one card is how a planning tool
loses trust.

⚠️ **A disclosure constraint found while checking.** `InitiateConfirmCampaignRequest.adgrid_cost_breakdown_json`
is marked *"INTERNAL — never surfaced on any FE-facing message; consumed only by the wallet
consumption report."* The per-cell AdGrid→AP breakdown is the agency's cost basis. Whether the
equivalent field on the *simulate* response carries the same restriction **needs confirming** —
Genie must not narrate it if so.

---

### B3 — Pre- or post-GST? ✅ ANSWERED — two legs, two treatments

| Leg | GST | Field |
|---|---|---|
| AdGrid → AdPartner (**the wallet debit**) | **ex-GST** | `adgrid_cost` — "base (ex-GST) persisted for the wallet debit" |
| AdPartner → Brand (what the agency invoices) | GST applies | `base_cost` (pre-GST) · `gst_percentage` · `gst_amount` · `total_cost_with_gst` |

So the wallet reservation is **ex-GST on the AdGrid leg**, and GST lives on the brand-facing leg.
The Planner reserves `adgrid_to_ad_partner_total_cost` with no GST adjustment.

---

### B4 — Is `InitiateConfirmCampaign` the OTP step? ⚠️ PARTIALLY — I had this wrong

`InitiateConfirmCampaign` / `CompleteConfirmCampaign` carry **no OTP field**. They are a
**reserve-then-confirm** pair:

```protobuf
InitiateConfirmCampaignResponse {
  int32 total_devices_reserved = 1;
  int64 expires_in_seconds = 2;            // ← the reservation has a TTL
  string total_cost = 3;
  repeated string start_date_unavailable_slots = 4;
}
```

The OTP is orchestrated **one layer up, in `ag-open-service`**: `CampaignOpenServiceImpl` uses both
`CampaignGrpcClient` and `OtpGrpcClient`, alongside `InitiateOtpActionResponse` and
`CampaignConfirmCompleteHandler`. So: reserve devices + `generateOtp` on step one; `validateOtp` +
`CompleteConfirmCampaign` on step two.

**Correction to `SERVICES.md` §4**, which attributed the OTP gate to the campaign RPCs directly.

#### The bigger find: a platform-wide OTP-gated action pattern

`OtpGrpcClient` is used by **nine** open-service implementations:

`CampaignOpenServiceImpl` · `WalletOpenServiceImpl` · `BatchOpenServiceImpl` ·
`ContractOpenServiceImpl` · `DeploymentPlanOpenServiceImpl` · `PromoBannerOpenServiceImpl` ·
`ClientAdminOpenServiceImpl` · `MemberServiceImpl` · `ClientServiceImpl`

There is a generic `InitiateOtpActionResponse` shape behind it. **This is the definitive list of
operations Genie can never complete**, and it is broader than the deck showed — wallet, batches,
contracts, deployment plans, promo banners, client admin and members are all OTP-gated too, not just
campaign approval.

**This strengthens the safety story considerably:** the human gate is not a campaign-approval
special case, it is a platform pattern, and Genie sits outside all nine.

**Action for S1:** the capability catalog records `otp_gated: true` on every capability touching
these nine, and the preflight refuses them with "this needs your OTP — here's the screen" rather
than starting a form.

---

### B5 — Per-metric freshness ⏳ PARTIAL

Established (`Knowledge.md` §9): camera-derived metrics refresh via the batch at **~6 hours**;
the batch is a **full recompute**, so figures can change retroactively.

**Open:** the per-metric mapping. Needed as an `as_of` source on each ontology entry. Best answered
while transcribing S1 — each metric's `source.api` implies its freshness class.

---

## A — The seven rulings

### R3 — Loop length: 60 s or 120 s? ✅ DISSOLVED — the question is malformed

`BillingCostCell` carries **`int32 loops_per_time_slot`** — **per cell**. Loop density is a property
of the batch / device type / time slot, not a platform constant.

So neither "60 s" nor "120 s" is the answer: **both design annotations are simplifications of
per-cell data.**

**Consequences:**

- **The Calculator must read `loops_per_time_slot` from availability data. It must not hardcode a
  loop length.** This is a design change to S6, and it is the single most valuable thing found in S0.
- The two annotations (`Avg Dwell` says 120 s; the Launch wizard says a 60-second loop holds 6 static)
  should be reworded as *illustrative*, or made cell-aware.
- **R3 needs no meeting.** It needs the annotations corrected to match the data model.

⚠️ Note the units: `loops_per_time_slot` is a *count of loops*, not a duration. Loop length is
derivable given slot length. Confirm the slot-length source when wiring S6.

---

### R1 · R2 · R4 · R5 · R6 · R7 — ⏳ OPEN, need human rulings

Unchanged from `BUILD.md` §3 Stage 0-A. These are genuine product decisions and no amount of code
reading settles them. **R7 first** — cross-tenant, correctness, and a Planner input.

---

## C — Access and environment · ⏳ ALL OPEN

| | Need | Owner |
|---|---|---|
| C1 | Keycloak JWKS URL, "me" endpoint, client-hierarchy endpoint | Backend |
| C2 | Test users, one per role, with obtainable JWTs | QA |
| C3 | Query access to `person_day` in QA | Data |
| C4 | Seeded adversarial QA tenant | QA |

---

## D — Your decisions · ⏳ ALL OPEN

| | Decision |
|---|---|
| D1 | LLM + embedding provider and region |
| D2 | AdGuide or Genie |
| D3 | Tier A corpus author |
| D4 | Roles in v1 |

---

## New questions raised by S0

| # | Question | Why it matters |
|---|---|---|
| N8 | **`simulate-cost` p95 latency** | The allocator may call it N times per plan. Cell-granularity pricing is only cheap if the call is |
| N9 | Does the *simulate* response's AdGrid-leg breakdown carry the same "INTERNAL, never FE-facing" restriction as the confirm message's? | Genie must not narrate the agency's cost basis if so |
| N10 | What is the reservation TTL (`expires_in_seconds`)? | A plan can go stale between planning and confirming |
| N11 | `start_date_unavailable_slots` — slots can close between plan and confirm | The plan card needs a validity caveat; the Planner should re-price before prefill |

---

## Round 2 — contradictions found on Inventory Management (image6)

Transcribing one more screen produced **five new conflicts**. This is the finding that matters most
from S1 so far: **the contradiction count is not 7, and a single rulings round will not clear it.**

Inventory Management, Standard tab, as drawn:

| | Value |
|---|---|
| Owned Screens | **10,000** (Rental \| Non-Premium) |
| Active Screens | **3,245** (68% utilisation) |
| Targeting-capable | **6,755** / 10k (64% camera + CV) |
| Monthly Reach | **7.70 Cr** (gross, 30-day) |
| Active Campaigns | **486** (distinct, across all slots) |
| Avg Targeting CPM | ₹201 (floor ₹167 · +20% on competition) |
| Avg Broadcasting CPM | **₹65** (Aggregated average of Static+Video) |
| Daily Impression | **~25.68 L** (Camera footfall \| **257/screen**) |
| Store Category donut | **3,458** Total Screens |
| Footfall donut | **32.8 M** Total Reach |
| City totals | 10,000 screens · **~2.1 M** daily footfall · 72% avg utilisation |

### R8 — Screen counts: 50,000 vs 10,000 vs 3,458 vs 3,245

Command Centre says **50,000** Live Screens. Inventory says **10,000** Owned. Its own donut says
**3,458** Total Screens while the Active Screens tile says **3,245**. Four numbers, two screens, one
of them self-inconsistent.

### R9 — Avg Broadcasting CPM: ₹95 vs ₹65

Identical label, near-identical subtitle ("Aggregated average of static & video" / "Static+Video"),
**different values**. One of them is wrong, or the label needs a scope qualifier.

### R10 — Footfall per screen: 300 vs 210 — **blocks the Planner**

The Command Centre's Network Reach card computes from **~300 footfall/screen/day**. Inventory shows
~2.1 M over 10,000 screens = **210/screen/day**, and its Daily Impression card says **257/screen**.
Three different per-screen rates.

**This is a direct Planner input** (`ARCHITECTURE.md` §2.1). A 30% error in footfall/screen is a 30%
error in every reach and impression figure the Calculator produces.

### R11 — City taxonomy differs between screens

| Command Centre | Inventory Management |
|---|---|
| Delhi · Greater Noida · Noida · Gurgaon · Faridabad · Ghaziabad | Delhi Region · Noida & Greater Noida *(combined)* · Gurgaon · Faridabad · Ghaziabad · **Other NCR** |

And AdGuide's plan card says **"Gurugram"** where both these say **"Gurgaon"**. Genie cannot name a
city until one taxonomy wins.

### R12 — Two reach numbers on one screen

"Monthly Reach **7.70 Cr** (gross, 30-day)" vs the footfall donut's "**32.8 M** Total Reach". Gross
vs de-duplicated is the likely explanation, but neither is labelled as such.

---

## A hypothesis that may resolve R7 — worth testing before escalating

**Targeting-capable is 64% on both screens** — 32,000/50k on the Command Centre, 6,755/10k on
Inventory. Same ratio, different base.

That is consistent with: **Command Centre = the whole AdGrid network the agency has contracts on;
Inventory Management = the screens the agency itself holds.** Two deliberate scopes, not a bug.

If that holds, R7's answer is that the Command Centre's audience panel is a **network-wide
aggregate**, not an `owner_id`-scoped query — which is why an `AD_PARTNER` with `ownsStores = false`
can see it. It would unblock the six camera metrics at once.

**Test it cheaply:** ask whoever owns the rollup whether a network-wide audience aggregate exists,
and confirm it does not leak per-store detail across tenants. Do not assume it — the ratio matching
could be coincidence, and the Inventory Premium/Standard toggle suggests a third scope exists.

---

## What this changes

Reconciling the design's numbers is **a workstream, not a decision round**. Recommendation:

1. **Keep transcribing breadth-first** — surface every conflict before convening anyone.
2. **Batch the rulings once**, when the sweep is done.
3. Treat R10 (footfall/screen) and R8 (screen counts) as **Planner blockers**, not cosmetic ones —
   they change what the Calculator outputs.
4. Accept that the mock data is not internally consistent, and that the authoritative answer for
   most of these is **the seeded QA tenant (C4), not the deck**. Several of these may dissolve the
   way R3 did, once real data exists.

---

## Round 3 — re-triage: the Figma numbers are mock

**Correction from the product owner (2026-09-18): the values in the deck are dummy data.** Only
terms, meanings and utilities carry signal.

That drops every conflict whose only evidence was two different numbers, and keeps every conflict
where the **same word means two things**, **two words mean the same thing**, or **a term is
ambiguous without a scope**. Numbering is unchanged so existing references still resolve.

### Dropped — value-only, no semantic content

| | Was | Why it's gone |
|---|---|---|
| R9 | Avg Broadcasting CPM ₹95 vs ₹65 | Identical label, identical subtitle. Only the mock values differed |
| — | Campaign counts 42 / 18 / 42 on the Command Centre | Mock inconsistency |
| — | Wallet balance ₹15.75L vs ₹12.30L vs ₹41.60L | Mock. The *concept* question survives as R4 |
| — | Screen counts 50,000 / 10,000 / 3,458 / 3,245 | Mock. The *terminology* question survives as R8 |
| — | Footfall per screen 300 / 210 / 257 | Mock. The *labelling* question survives as R10 |
| — | Monthly Reach 7.70 Cr vs 32.8 M | Mock. The *gross vs de-duplicated* question survives as R12 |

**Consequence:** the deck is authoritative for **vocabulary only**. For anything numeric the
authority is the seeded QA tenant (**C4**) or the code. This makes C4 more urgent, and makes the
remaining transcription sweep faster — stop diffing values entirely.

---

### Class A — one term, two meanings *(the dangerous class: one must be renamed)*

| | Term | Meaning 1 | Meaning 2 |
|---|---|---|---|
| **R1** | **Avg Attention** | Command Centre: a **ratio** — `faces looking ÷ faces present`, unit % | content-performance: a **duration** in seconds, dwell-derived. `definitions.md` flags it unresolved as "decision #1" |
| **R2** | **Morning / Afternoon / Evening / Night** | Analytics: 6a–12p · 12–5p · 5–10p · 10p–6a — covers **24 h** | Booking wizard: 10AM–1PM · 1–4PM · 4–7PM · 7–10PM — covers **12 h** |
| **R12** | **Reach** | "Monthly Reach — **gross**, 30-day" (repeats included) | "Total Reach" on the same screen, unlabelled — presumably de-duplicated |
| **R13** *(new)* | **Active Campaigns** | Inventory: "**distinct, across all slots**" | Command Centre: a plain count, qualified only by "*n* paused" |

R2 carries a second, structural question: does the network sell **24 hours or 12**? The two sets do
not merely disagree on boundaries, they disagree on total operating hours.

### Class B — two terms, one thing *(pick one; alias the other)*

| | Terms | Where |
|---|---|---|
| **R5** | **Inventory Management** vs **Screen Management** | Nav label differs between frames; and on the Inventory frame the nav tab and the page H1 disagree with each other |
| **R11** | **Gurgaon** vs **Gurugram**; **Noida / Greater Noida** separate vs combined; **Other NCR** exists on one screen only | Command Centre · Inventory · AdGuide plan card — three different city taxonomies |
| **R8** | **Live Screens** · **Owned Screens** · **Active Screens** · **Total Screens** · **Targeting-capable screens** | Are these five concepts, or fewer concepts under different names? Each needs a definition or an alias |

### Class C — the term is ambiguous without a scope qualifier

| | Term | The ambiguity |
|---|---|---|
| **R7** | Command Centre's **audience metrics** (Network Reach, Audience Profile, Location Distribution) | `dashboard_views` keys on `owner_id`; `AD_PARTNER` has `ownsStores = false`. **Hypothesis:** CC is a *network-wide* aggregate ("camera-measured, whole network") while Inventory is *agency-owned* ("Owned Screens · Rental \| Non-Premium"). The subtitles support this — it is now a label argument, not a number argument. Needs confirming that such an aggregate exists and is tenant-safe |
| **R4** | **Wallet Balance** on the Command Centre | Three distinct concepts exist on the Wallet screen: Main Wallet Balance · In Team Sub-Wallets · Total Credits owned. Which one is the CC card? |
| **R14** *(new)* | **Premium vs Standard** | Inventory is tier-scoped by a toggle; the Command Centre has no toggle. Are CC's metrics blended, Standard-only, or whole-network? |
| **R10** | **Daily Impression**, subtitled "*Camera footfall \| n/screen*" | The Command Centre distinguishes **footfall** (people) from **impressions** (ad-views = reach × ad-views/visitor). Inventory labels an impressions metric with a footfall subtitle. One of the two is mislabelled |

### Class D — utilities: what a role can actually do

| | Item | Type |
|---|---|---|
| **R6** | `CAMPAIGN_MANAGER` sees **Top up** and **Settle**, but `PermissionBundles` grants that role neither `TOPUP_WALLET` nor `MARK_INVOICE_PAID` | **Ruling** — the bundle is wrong or the screen is |
| — | The CM's AdGuide opener promises approvals the role cannot perform | **Engineering fix**, not a ruling — computed per role in S9 |
| — | 8 permissions are declared but never used in an `@RequireAccess` (`FINDINGS.md` F1) | **Audit task** — read the real gate per controller in S1 |

---

### Tally

| | |
|---|---|
| Rulings open | **11** — R1 · R2 · R4 · R5 · R6 · R7 · R8 · R10 · R11 · R12 · R13 · R14 |
| Dissolved by reading the data model | 1 — R3 |
| Dropped as mock-value artifacts | 1 numbered (R9) + 5 unnumbered |
| Engineering fixes, not rulings | 2 |

Every remaining item is a question about **what a word means**, **which word to use**, or **what a
role may do**. None of them can be settled by looking at more mock data — but several may be settled
by reading code (as R3 was), which should be attempted before convening anyone.

---

## Round 4 — settling rulings from the analytics code

Source: `ag-analytics-service/dto/response/{AudienceWindow,ContentWindow,ContentPerformance,CampaignMetricsResponse}.java`.
These DTOs are the platform's own vocabulary, and they settle three rulings outright.

### R1 — Avg Attention ✅ SETTLED (math), naming decision is now cheap

Both quantities exist and are both computed correctly. **There was never a computation ambiguity —
only a naming collision, and the code already disagrees with itself about the names.**

| DTO | Field | Meaning | Unit |
|---|---|---|---|
| `AudienceWindow` | `avgAttention` | the **ratio** | **0–1** |
| `AudienceWindow` | `avgDwellS` | dwell | seconds |
| `ContentPerformance` | `avgAttnS` | *"mean dwell seconds"* — **a dwell, despite the name** | seconds |
| `ContentPerformance` | `engagedPct` | *"attn_sum/attn_n x 100"* — **the ratio, under a different name** | **0–100** |

`AudienceWindow`'s javadoc is explicit: *"`returnRate`, `share` and `avgAttention` are **0–1** …
Dwell is seconds."*

**So the Command Centre is right.** Its "Avg Attention = faces looking ÷ faces present" is
`AudienceWindow.avgAttention`, and its "Avg Dwell = 75s" is `avgDwellS`. The **content-performance
screen is the one that is misnamed** — its "AVG ATTN 4.8s" is `avgAttnS`, which the code's own
comment calls *mean dwell seconds*.

**Recommended ruling — the cheap direction:**
- **"Attention" means the ratio.** Keep the Command Centre as drawn.
- Rename `ContentPerformance.avgAttnS` → `avgDwellS`, and the content UI's "AVG ATTN" → **"Avg Dwell"**.
- Surface `engagedPct` as **"Engaged %"**, as the field already implies.

One screen's labels change instead of the platform's vocabulary.

⚠️ **Unit trap for Genie:** the same ratio is **0–1** in `AudienceWindow` and **0–100** in
`ContentPerformance`. The ontology records the unit per source, and the DATA path must not assume.

---

### R12 — "Reach" ✅ SETTLED — there are **four** reach concepts, and only one is window-unique

| DTO | Field | Comment | What it really is |
|---|---|---|---|
| `AudienceWindow` | `uniqueVisitors` | *"distinct persons (exact, from person_day)"* | **window-unique, exact** |
| `ContentWindow` | `reach` | *"conservative: max single-day distinct reach (not additive)"* | max-daily |
| `ContentPerformance` | `reach` | *"max single-day distinct persons"* | max-daily |
| `CampaignMetricsResponse` | `dailyReach` | *"footfall per day"* | **footfall, not persons** |
| `CampaignMetricsResponse` | `estimatedMonthlyReach` | *"dailyReach × days in current IST month"* | **gross — no de-duplication at all** |

So the Command Centre's "Monthly Reach — *gross*, 30-day" is almost certainly
`estimatedMonthlyReach`, which multiplies a daily figure by days and de-duplicates nothing.

**The consequence for S6 is the important part.** The plan card promises *"Reaches 8.3 L unique
people"* — window-unique reach. The only window-unique figure the platform computes is
`AudienceWindow.uniqueVisitors`, which is **owner-scoped and only covers the caller's own stores**.
For a campaign planned across screens the agency does not own, **no window-unique reach exists today.**

**This makes S5 mandatory rather than prudent.** Building the frequency curve from `person_day` is
not an optimisation — without it the Planner cannot state a de-duplicated reach at all, and would
have to either quote a gross number (wrong, and wrong in the flattering direction) or a max-daily
number (severely understated).

---

### R10 — footfall vs impressions ✅ SETTLED — Inventory's subtitle is wrong

| Field | Comment |
|---|---|
| `AudienceWindow.footfall` | *"total events (visits) in the window"* — people-events |
| `ContentPerformance.impressions` | *"playback events — **NOT person data**, survives suppression"* |

They are different quantities measuring different things, and the code says so explicitly.
Inventory Management's **"Daily Impression"** card is subtitled *"Camera footfall | n/screen"* —
an impressions metric labelled with a footfall subtitle. **Fix the subtitle.**

Related misnomer worth fixing while there: `CampaignMetricsResponse.dailyReach` is *named* reach and
*is* footfall per day.

---

### R7 — ⚠️ WORSE, and more useful: this may be a missing capability, not a naming question

`AudienceWindow`'s javadoc: *"Scoped by the store **OWNER**: this counts everyone who walked into
**the client's stores**"*, and `clientId // echo of the request (== the store owner)`.

Its `scope` field is *"the CITY, or **"Network"** when unfiltered"* — where "Network" means **all of
this owner's stores**, not the whole AdGrid network.

An `AD_PARTNER` has `ownsStores = false`, so **`AudienceWindow` returns nothing for an agency.**

**The round-2 hypothesis — that the Command Centre is served by some network-wide audience
aggregate — has no supporting code.** No such DTO appears in the analytics response set. So R7 is
not "which of two existing scopes is it"; it is:

> **Does a network-wide audience aggregate exist anywhere, or does the Command Centre's audience
> panel require one to be built?**

If it must be built, it is a **new analytics capability with a cross-tenant surface**, and it lands
on the critical path for the Command Centre — the screen Genie opens on. Escalate accordingly.

---

### Also found — a Genie behaviour requirement

Every audience/content DTO carries `suppressed` + `reason` (k-anonymity). The javadoc: *"When
`suppressed` is true only the identity + window fields are meaningful."*

**Genie must treat a suppressed response as "too few people to report", never as zero.** This needs
an ontology field per metric and a dedicated eval case — reporting a suppressed metric as 0 is a
confident wrong answer about someone's campaign.

---

### Tally after round 4

| | |
|---|---|
| Settled from code | **R1** (naming direction recommended) · **R10** · **R12** · R3 (round 1) |
| Sharpened, escalate | **R7** — possibly a missing capability |
| Still open, need humans | R2 · R4 · R5 · R6 · R8 · R11 · R13 · R14 |
| New requirement | k-anonymity suppression handling |

**Open rulings: 8**, down from 11. Awaiting the definitions file to confirm R1's naming direction.

---

## Round 5 — `AUDIENCE_DASHBOARD_METRICS.md` (contract v3.1)

Source: `C:\Work\Adgrid\AWS\AUDIENCE_DASHBOARD_METRICS.md`. This is the authoritative definitions
document for `POST /api/v1/dashboard/audience`.

### R7 ✅ RESOLVED — there is no network-wide audience aggregate, and one is not needed

The document is unambiguous:

> *"Audience is **scoped by `owner_id`** (= the request's `clientId`): everyone who entered your
> stores, whoever's creative was on screen."* … *"Audience always filters `owner_id`."*

And §6 lists every known gap — **no network-wide aggregate is among them, because none exists.**

**The concrete, testable consequence:** an `AD_PARTNER` (`ownsStores = false`) calling
`/dashboard/audience` with its own `clientId` as `owner_id` matches **zero** `dashboard_views` docs
→ `uniqueVisitors = 0` → `suppressed: true`, `reason: "k-anonymity: 0 < 10 people"`. The Command
Centre's audience panel would render an empty state today.

#### The resolution — the agency's audience is *content*-scoped, not *owner*-scoped

An agency has no stores, so "everyone who entered your stores" is meaningless for it. The right
question for an `AD_PARTNER` is **"who saw my ads"** — which is exactly what `content_views` holds,
scoped by `client_id`.

And the demographic data already exists there. `content_views` stores `age_counts`, `gender_counts`,
`hour_counts[24]`, `dwell_sum_ms`/`dwell_n`, `attn_sum`/`attn_n` — the same raw material
`dashboard_views` holds, at a `client_id` grain.

**But `ContentWindow` does not expose it.** Its fields are `impressions`, `reach`, `storesPlayed`,
`devicesPlayed`, `totalViews`, `avgDwellS`, `avgAttention`, `dwellByCohortZone` — **no
`genderSplit`, no `ageDistribution`, no `hourRhythm`.**

**So R7's answer is: expose stored fields that already exist, rather than build a new aggregation.**

| Option | Cost | Verdict |
|---|---|---|
| Build a network-wide audience aggregate | New cross-tenant aggregation + a new privacy surface | **No** — invents a tenancy that does not exist |
| **Extend `ContentWindow` with the demographics `content_views` already stores** | Additive: map existing fields onto the response | ✅ **Recommended** |
| Leave the panel out for `AD_PARTNER` | Free | Fallback if the above is deprioritised |

⚠️ The k-anonymity floor must apply on the content grain too. And note `ContentWindow.reach` is
*max single-day distinct*, not window-unique (R12) — so a content-scoped audience panel inherits
that limitation.

**Action:** put this to the analytics backend as a proposal, not a question. It is a small additive
change on a screen that is otherwise blocked.

---

### R1 ✅ CONFIRMED by the contract

> `avgAttention` — *"avg attention score"* — `Σ attn_sum / Σ attn_n`, **0–1**, no scaling
> `avgDwellS` — *"avg dwell per detection"* — `Σ dwell_sum_ms / Σ dwell_n / 1000`, **seconds**

Exactly as round 4 determined. The Command Centre is right; the content screen's "AVG ATTN 4.8s" is
the misnamed one. §3's scale reference also confirms the **0–1 vs 0–100** trap between the two DTOs.

---

### R2 ⏳ SHARPENED — analytics has no daypart concept at all

`hourRhythm{0…23}` — *"footfall by hour of day (IST); all 24 keys always present"*. There is **no
daypart bucketing anywhere in the audience pipeline.** Dayparts exist only on the booking side, as
the `TimeSlots` enum (`MORNING`/`AFTERNOON`/`EVENING`/`NIGHT`) with capacity tracked per daypart.

**Recommended ruling:** the **booking** definition is canonical, because it is the one with
operational meaning — inventory is sold, reserved and priced per daypart. The Command Centre's
"Daypart Occupancy" is a presentation-layer bucketing of `hourRhythm` and should be re-bucketed to
match the booking boundaries. Still needs the boundary values themselves, which live in
campaign-service.

---

### R15 🆕 NEW — age bucket taxonomy conflicts with the design

| | Buckets |
|---|---|
| **Backend** (`rollup._AGE_BUCKETS`) | `18-25` · `26-35` · `36-50` · `50+` — **four** |
| **Command Centre Figma** | 18–24 · 25–34 · 35–44 · 45–54 · 55+ — **five** |

Different count *and* different boundaries. This is structural, not a mock-value artifact.

The contract also warns: *"First match wins, so a 50-year-old lands in `36-50`, and `50+` actually
holds **51+**."* A relabel to `51+` is planned, boundaries unchanged. **Treat the four keys as opaque
strings** — Genie must never parse a bucket label to infer its range.

---

### Genie requirements this document generates

Beyond the rulings, six behaviours that are now specified rather than guessed:

| # | Requirement |
|---|---|
| G1 | **k-anonymity floor is 10**, applied to the whole window on distinct persons. A suppressed payload has scalars `0`, maps `{}`, arrays `[]`. **Genie renders "too few people to report" from `suppressed`, never from the zeros.** Needs its own eval case |
| G2 | **`stores[].uniqueVisitors` does not sum to `uniqueVisitors`** — the gap is exactly `crossStoreRepeaters`. Genie must never total store rows. Distinct-person counts are never additive |
| G3 | **`returning` counts detections, not distinct days.** A days-based definition was explicitly considered and rejected. Genie must not say "came back on another day" |
| G4 | **`delta` is `null` when the previous window is empty or below the k-floor** — deliberately, to stop a suppressed count being recovered by subtraction. Genie must not read `null` as "no change" |
| G5 | **`crossStore` counts stores; `crossDevice` counts screens.** A historical bug merged them. Never conflate |
| G6 | **Nothing monetary exists in the analytics pipeline** (§6). Every revenue figure comes from billing — which confirms the freshness split for B5: camera metrics ~6 h, money live |

Plus two smaller ones: `peakRatio` averages over **active hours only**, not all 24; and the window
guard rejects spans **> 366 days**, which Genie's date resolution must respect.

---

## Status after round 5

### Resolved or dropped — 6

| | Ruling | How |
|---|---|---|
| R1 | Avg Attention | Settled from code, confirmed by contract v3.1 |
| R3 | Loop length | Dissolved — per-cell `loops_per_time_slot` |
| R7 | AD_PARTNER audience scoping | **Resolved** — extend `ContentWindow` with demographics `content_views` already stores |
| R9 | CPM values | Dropped — mock data |
| R10 | Footfall vs impressions | Settled — Inventory's subtitle is wrong |
| R12 | Reach | Settled — four concepts; only `uniqueVisitors` is window-unique |

### Remaining — 9

| | Ruling | Owner | Note |
|---|---|---|---|
| R2 | Daypart boundaries | Design + campaign backend | Recommendation ready: booking definition wins. Needs the boundary values |
| R4 | Which "Wallet Balance" | Design + billing | Main / sub-wallets / total |
| R5 | Inventory vs Screen Management | Design | Naming |
| R6 | CM sees Top up / Settle | Design + backend | Bundle wrong, or screen wrong |
| R8 | Live / Owned / Active / Total Screens | Design | Five terms, how many concepts |
| R11 | City taxonomy | Design | Gurgaon/Gurugram; Noida split; "Other NCR" |
| R13 | "Active Campaigns" definition | Design | "distinct across all slots" vs plain count |
| R14 | Premium vs Standard scope on CC | Design | Blended, Standard-only, or network |
| R15 | **Age bucket taxonomy** 🆕 | Design + analytics | 4 backend buckets vs 5 in the Figma |

**Seven of the nine are pure design decisions** needing no engineering input. R2 and R15 need a
backend voice as well.

---

## Round 6 — `CONTRACT_REVIEW_RESPONSE.md` (contract v3.1, the cited authority)

### R7 ✅ RESOLVED — and materially worse than round 5 concluded

§5 *Scope boundary — confirmed* states it outright:

> **These endpoints serve a NETWORK_PARTNER only.**
> - `/audience` is scoped by the **store owner**
> - `/content` is scoped by the **creative owner**
> - *"The **ad-partner** and LESSPAY_MERCHANT dashboards are **separate products**. The columns exist
>   so those can reuse this data later, but **nothing here serves them.**"*

**Correction to round 5.** I proposed extending `ContentWindow` with demographics, on the assumption
that the content endpoint already served agencies. It does not. **Neither `/audience` nor `/content`
serves an `AD_PARTNER` today.** The entire v3.1 analytics contract is built for `NETWORK_PARTNER`.

#### What this actually means

**The whole camera-analytics half of the Command Centre has no backing API.** Not just the audience
panel — every CAMERA-badged metric: Network Reach, Avg Attention, Avg Dwell, Verified Impressions,
Audience Profile, and Impressions Delivered.

The data model anticipates it. `content_views` is keyed by `client_id` and an `AD_PARTNER` is a
client; §5 notes `brandId` *"is populated only on ad-partner rows, which you never receive"* — so
ad-partner rows **exist in the data** and are filtered out for network partners.

**So: the data is there. The serving is not.** Good news for feasibility, bad news for schedule.

| | |
|---|---|
| R7 is no longer a Genie question | It is a **roadmap** question: *when does the ad-partner analytics product ship?* |
| Escalate to | Backend/product owner, not design |
| Blocks | 6 Command Centre metrics, and the camera half of S4 |

#### Impact on `BUILD.md`

- **S4 (DATA path) is partially blocked.** The camera metrics have no endpoint for this tenant.
- **The R1 demo survives** — billing, campaign, wallet and inventory metrics all have working APIs
  (`SERVICES.md` §4). Demo on those; mark camera metrics "not available for your account yet".
- **S6's reach model is unaffected** — it reads `person_day` directly (S5), not through `/audience`.

---

### R15 ✅ RESOLVED — the contract already forbids what the design does

> ```python
> _AGE_BUCKETS = (("18-25", 18, 25), ("26-35", 26, 35), ("36-50", 36, 50), ("50+", 51, 200))
> ```
> *"First match wins, so age 50 → `36-50`, and `50+` actually contains 51 and over."*
> *"**Fix:** the label will change to `51+`. Boundaries unchanged, so no number moves."*
> *"Treat the four bucket keys as **opaque strings** supplied by the API and render them as given;
> **do not hard-code them**."*

The Figma's five buckets (18–24 / 25–34 / 35–44 / 45–54 / 55+) are hard-coded and do not match the
API. **The contract already rules on this** — render what the API returns. No decision needed; the
design has to change.

**Genie requirement (G7):** never parse, re-label or re-bucket an age key. Pass through as given.

---

### A product insight worth acting on — §7 "Fields available but currently unrendered"

The contract lists fields the backend **computes today that no screen displays**:

`footfall` · `returning` (only `returnRate` is shown) · `crossStoreRepeaters` ·
`crossDeviceRepeaters` · `avgAttention` · `stores[]`

**Genie can answer questions the dashboard cannot show.** "How many people visited two or more of my
stores?" has an exact answer that appears on no screen. That is a real differentiator, it costs
nothing extra to build, and it is the kind of thing that makes people open the panel twice.

**Action for S1:** the ontology gains an `unrendered: true` flag — a metric that Genie can serve but
that has no widget. It is also a natural backlog for the FE.

---

## Status after round 6

### Resolved or dropped — 7

| | Ruling | How |
|---|---|---|
| R1 | Avg Attention | Code + contract v3.1 |
| R3 | Loop length | Dissolved — per-cell `loops_per_time_slot` |
| R7 | AD_PARTNER analytics scope | **Resolved — no ad-partner analytics product exists yet.** Roadmap escalation |
| R9 | CPM values | Dropped — mock |
| R10 | Footfall vs impressions | Code |
| R12 | Reach | Code — four concepts |
| R15 | Age buckets | Contract mandates opaque keys; the design must change |

### Remaining — 8, and **all eight are pure design decisions**

| | Ruling | Note |
|---|---|---|
| R2 | Daypart boundaries | Recommendation ready: booking wins. Needs boundary values from campaign-service |
| R4 | Which "Wallet Balance" | Main / sub-wallets / total |
| R5 | Inventory vs Screen Management | Naming |
| R6 | CM sees Top up / Settle | Bundle wrong, or screen wrong |
| R8 | Live/Owned/Active/Total Screens | Five terms, how many concepts |
| R11 | City taxonomy | Gurgaon/Gurugram; Noida split; "Other NCR" |
| R13 | "Active Campaigns" definition | "distinct across all slots" vs plain count |
| R14 | Premium vs Standard scope on CC | Blended / Standard-only / network |

No further code reading will settle these. **The rulings brief can now go out.**

---

## Round 7 — sweep: Brand Management, Campaign Detail, Team Management

Terms and utilities only; values ignored throughout.

### R2 ✅ EFFECTIVELY RESOLVED — the campaign detail screen settles it

The Broadcasting campaign detail uses the **booking** dayparts and states the operating day outright:

- Day-part filter chips: **Morning · Afternoon · Evening · Night** (Night greyed out)
- "When your audience is watching" — *"hourly delivery · **10AM–10PM**"*, axis 10a…9p
- Band labels: **Morning 10–1 · Afternoon 1–4 · Evening 4–7 · Night 7–10 ("not sold")**
- Caption: *"Evening (4–7PM) carries the peak footfall and highest frequency. Night (7–10PM) sits
  outside this campaign's booked day-parts."*

**Two screens now use the booking definition** (Launch wizard, campaign detail) **against one using
the analytics one** (Command Centre "Daypart Occupancy"). And the network's operating day is
**10AM–10PM, 12 hours** — so the Command Centre's 24-hour banding (6a–12p, 10p–6a) spans hours that
are never sold.

**Recommendation upgraded from "likely" to "near-certain": booking definition, four 3-hour bands,
10AM–10PM.** The Command Centre chart is the outlier and should be re-bucketed. Still needs the
boundaries confirmed from campaign-service, but design need only ratify.

### R7 ⬆ REINFORCED — the design assumes client-scoped camera data everywhere, not just on Home

- Campaign detail carries a full **Audience Profile · CAMERA** panel (gender, age bands) **per campaign**
- Brand detail carries **"Total Audience Reach / day"** **per brand**

Both are `client_id`-grain, which is exactly the `content_views` grain proposed in the R7 escalation.
This is not one widget on one screen — **camera-derived audience is woven through the whole
ad-partner product.** Worth adding to `asks/R7-AD-PARTNER-ANALYTICS.md` as evidence that option C
(descope) removes more than it looks like.

### R5 ⬆ GENERALISES — nav label vs page heading is a systematic mismatch

| Nav | Page H1 |
|---|---|
| Inventory Management | **Screen Management** |
| Team Management | **User Management** |

Not a one-off. R5 should be asked as a **convention** question — "should the nav label and page
heading match?" — rather than as a single naming choice.

### R8 ⬆ GROWS — a sixth screen term

Brand detail adds **"Screen Used"** to: Live Screens · Owned Screens · Active Screens ·
Total Screens · Targeting-capable screens.

### R11 ✅ CONFIRMED — a genuine 2-2 split

| Gurgaon | Gurugram |
|---|---|
| Command Centre · Inventory Management | Brand Management · AdGuide plan card |

---

## New conflicts

### R20 🆕 — the design's permission model does not exist in the backend · **highest severity**

"Add New User" (Team Management) grants access along **two dimensions**:

| Control | Values |
|---|---|
| **Inventory Access** * | Premium Inventory · Standard Inventory |
| **Campaign Access** * | Broadcasting Campaigns · Targeted Campaigns — *"Which campaign types this manager is allowed to create."* |

The backend `Permission` enum is **per-operation**: `CREATE_CAMPAIGN`, `EDIT_CAMPAIGN`,
`LAUNCH_CAMPAIGN`, `UPLOAD_CREATIVE`, and so on. There is **no tier dimension and no motion
dimension** — nothing resembling `ACCESS_PREMIUM_INVENTORY` or `CREATE_BROADCASTING_CAMPAIGN`.

**Two possibilities, and they need separating:**

1. The gate exists somewhere I have not found (a column on the user/access row rather than a
   `Permission`), or
2. **it is a UI-only restriction.**

If (2), a Campaign Manager limited to "Standard Inventory" in the UI would still be able to create a
Premium campaign through the API — and Genie, which forwards the user's own JWT, would succeed in
doing so on their behalf.

**I am not asserting a vulnerability — I have not tested it.** But it must be verified before Genie's
capability catalog records any tier or motion gate, because Genie's preflight can only mirror a gate
the backend actually enforces.

**Action:** add to the backend ask. This is the one new item that is not a design decision.

### R16 🆕 — three "verified" terms

| Term | Where |
|---|---|
| **Verified Impressions** | Command Centre — *"Share of served impressions the camera confirmed a real, present person actually saw"* |
| **Verified view rate** | Campaign detail KPI |
| **verified viewers/day** | Campaign detail header chip |

Two or three distinct concepts? At minimum "verified impressions" (a share of impressions) and
"verified viewers" (a count of people) are different units.

### R17 🆕 — campaign status vocabulary

| Where | Values seen |
|---|---|
| Brand detail → Recent Campaigns | **Active** · **Scheduled** |
| Campaign detail header | **Live** · **Broadcasted** |
| Command Centre | "42 active, 12 **paused**" |

Is "Live" the same state as "Active"? Is "Broadcasted" a status or a motion? One state machine, one
vocabulary, please.

### R18 🆕 — four names for impressions per day

**Impressions Delivered** (Command Centre) · **Total Daily Impressions** (brand detail) ·
**Daily Impression** (inventory) · **Impressions** (campaign detail). Low severity, easy fix.

### R19 🆕 — two industry taxonomies on one card

Brand Summary shows **Brand Type: FMCG**; Company Information on the same screen shows
**Industry: Food & Beverages • Soft Drinks**. Two classifications of the same brand, side by side.

---

## Free wins from this sweep

**The pacing advisor is fully specified by the design.** Campaign detail's "Delivery & Pacing" panel
gives the exact semantics:

> 67% DELIVERED · Time elapsed 67% · 20/30 days · Projected final 78.75L · 100%
> Status: **"On pace · even delivery — projected to complete on Apr 30"**
> *"The marker shows time elapsed. Delivery tracking the marker = healthy pacing; ahead = burning
> fast; behind = under-delivering."*

That is `budget_pacing` and `underdelivery` (`ARCHITECTURE.md` §3) defined for us — thresholds,
projection method and the three states. **Lift it directly into the advisor spec in S9.**

**Frequency is a first-class product concept.** Campaign detail shows Impressions, Reach and
**Frequency 2.00x** together, with reach = impressions ÷ frequency. The Planner's reach model
(`ARCHITECTURE.md` §2.1) uses the identical identity — the design and the model agree, which is
reassuring for S6.

**"Premium" is deprioritised.** The Brand Management frame is annotated *"PREMIUM (NOT PRIORITY WILL
DISCUSS LATER)"*. That may simplify **R14** and narrows S6's first cut to Standard inventory.

---

## Structural facts for the ontology

- **Campaigns live under Brand Management**, not as a top-level nav item. Route shape:
  `/brands/{brandId}/campaigns/{campaignId}`. Brand detail carries "Manage campaigns" and
  "Create Campaign" as its primary actions.
- **Three campaign detail variants**: Broadcasting · Targeted · Premium, each with a different
  widget set.
- **Users are Employee or Marketing Agency** — internal staff vs external partner. This distinction
  does not appear in `PlatformRole` and may need an ontology field.
- **Add New User creates only Campaign Managers** (the role is fixed on the form). `BRAND_MANAGER`
  exists in the backend but has no creation path in this UI.
- **Sub-wallet funding happens at user creation** — *"Transferred from the admin wallet into this
  user's sub-wallet on creation"* — and top-ups are OTP-gated, consistent with the nine-service
  pattern.

---

## Running tally after round 7

| | |
|---|---|
| Resolved or dropped | **8** — R1 · R2 · R3 · R7 · R9 · R10 · R12 · R15 |
| Open, design decisions | **11** — R4 · R5 · R6 · R8 · R11 · R13 · R14 · R16 · R17 · R18 · R19 |
| Open, backend verification | **1** — R20 |

R2 moves to resolved on evidence; design need only ratify the recommendation.

**Still unswept:** Creative Library (image4), Profile / Terms (image10), the Approvals detail
(image2), the remaining Launch wizard steps and its Targeted/Premium variants (image5), Inventory's
rate-management and availability screens (image6), and the Campaign Manager variants (image12–15).

---

## Round 8 — sweep: Launch wizard steps 2 & 4, Creative Library

### R11 ⬆ DECIDED ON EVIDENCE — Gurugram wins 4–2

| **Gurugram** | **Gurgaon** |
|---|---|
| Brand Management · AdGuide plan card · Launch step 2 · Launch step 4 | Command Centre · Inventory Management |

And a **fourth city taxonomy** appears in Launch step 2: Delhi · Gurugram · Noida · Faridabad ·
Ghaziabad — **five cities, no Greater Noida, no "Other NCR"**. So the four taxonomies are:

| Screen | Cities |
|---|---|
| Command Centre | Delhi · Greater Noida · Noida · Gurgaon · Faridabad · Ghaziabad (6) |
| Inventory | Delhi Region · Noida & Greater Noida · Gurgaon · Faridabad · Ghaziabad · Other NCR (6) |
| AdGuide plan | Delhi · Gurugram · Noida · Faridabad · Ghaziabad · Other NCR (6) |
| **Launch wizard** | Delhi · Gurugram · Noida · Faridabad · Ghaziabad (**5**) |

**The Launch wizard list is the one that matters** — it is what a user actually buys. Recommend
adopting it as canonical and treating the rest as rollups.

---

### Confirmed for the Planner (S6): the allocation grain is **city × store category**

Launch step 2 "Select Screens" is the allocation UI, and it selects by:

**city → store category → screen count**, with categories Grocery/Kirana · Pharmacy · Electronics ·
Clothing · Dairy/Bakery · Others.

That maps exactly onto `BillingCostCell`'s `(city, shop_type, device_count)` dimensions. **The
design and the billing contract agree on the unit of work**, which is the single best piece of news
for S6 so far — the allocator's output shape is already the wizard's input shape, so `prefill` is a
direct field mapping rather than a translation.

Step 2 also shows a **live cost summary that recalculates as sliders move**: Total Campaign Cost,
Base/Image/Video/Daily Cost, Avg CPM, Impressions, Daily Reach, plus By City and By Category
breakdowns.

**This strongly implies `simulate-cost` is fast enough to call interactively** — the product already
assumes it. Does not replace the measurement asked for in `BACKEND-CHECKS.md` §2, but it shifts the
prior heavily toward "call it freely".

---

## New conflicts

### R21 🆕 — "Activate" or "Submit for approval"? · **high severity**

| Where | Terminal action |
|---|---|
| Launch wizard, step 4 | **Activate** — *"Review everything before activating. The campaign goes live on the scheduled start date."* |
| AdGuide prefill modal | **Submit for approval** |
| Notifications & Approvals | a campaign approval queue, gated by OTP + acknowledgement |

Three different endings for the same flow. The likely reconciliation is **role-dependent** — an
`AD_PARTNER_ADMIN` activates directly while a `CAMPAIGN_MANAGER` submits for approval — but nothing
in the deck says so.

**This directly determines what AdGuide's prefill hands off to**, and its button label must match
what the user can actually do. Needs a ruling.

### R22 🆕 — when is the wallet actually debited? · **high severity, blocks the Planner**

Launch step 4 carries a **Settlement Notice**:

> *"This campaign will be settled against your existing **screen rental agreement**. No additional
> payment to AdGrid is required."*

So on a rental agreement, **no wallet debit occurs**. But AdGuide's plan card says *"Reserves ₹2.50 L
from ₹12.30 L · ₹9.80 L left"*, which assumes a debit always happens.

Command Centre shows three commercial models on Top Brands — **Rev-Share · Rental · Mixed**. The
Planner needs to know which model applies before it can state a wallet impact at all.

**Until this is settled, the plan card's reservation line may be wrong for rental-model brands.**

### R24 🆕 — can pending creatives be used?

| Where | Statement |
|---|---|
| Creative Library banner | *"Only **approved** creatives are available to attach when you launch a campaign"* |
| Launch step 4 tip | *"You can activate now even with **pending** creatives — they'll go live once approved."* |

Step 4 does show Pending assets attached to the campaign. Either the library banner is wrong, or
"attach" and "activate" are different gates. Low effort to resolve, and AdGuide will be asked this.

### R25 🆕 — creative dimensions: landscape or portrait?

| Where | Spec |
|---|---|
| Launch step 1 | Static Ad **1920×1080** · Video Ad **1920×1080** |
| Upload Creative modal | Static **1080×1920 px (max)** · Video **1080×1920 px** |

Landscape versus portrait. Not a mock-value issue — a spec conflict, and the wrong answer means
rejected uploads.

---

## New concepts for the ontology

**Creative approval is its own three-state machine**, separate from campaign approval:
**Approved (In Library) · Pending Review · Rejected**. The Library Summary counts each, plus
Static/Video split and last-upload date.

**Creative Library is per-brand**, nested under Brand Management — reached from the brand detail
alongside "Manage campaigns" and "Create Campaign". Not a global library.

**Creative Guidelines** ship as a Do's / Don'ts list on the screen. That is ready-made RAG content
for "how should I make a creative" — worth lifting into the knowledge base rather than paraphrasing.

**Campaign cost is presented brand-facing only.** Step 4 shows **"Cost to Brand / Invoice Amount"**
with a **Base Amount** beneath it — the AP→Brand leg with and without GST. **The AdGrid→AdPartner leg
never appears**, consistent with the *"INTERNAL — never surfaced on any FE-facing message"* comment
on that breakdown. Partly answers `BACKEND-CHECKS.md` §3 from the design side.

**Final Checklist** on step 4 — Details complete · Screens selected · Creatives uploaded ·
All approved · Terms accepted. A readiness model AdGuide can report against directly ("you're two
items short of activating").

---

## Running tally after round 8

| | |
|---|---|
| Resolved or dropped | **9** — R1 · R2 · R3 · R7 · R9 · R10 · R11 · R12 · R15 |
| Open, design decisions | **13** — R4 · R5 · R6 · R8 · R13 · R14 · R16 · R17 · R18 · R19 · R21 · R24 · R25 |
| Open, backend verification | **2** — R20 · R22 |

R11 moves to resolved on evidence (Gurugram 4–2, Launch wizard list canonical); design need only
ratify, same as R2.

**R21 and R22 are the two that matter** — both change what AdGuide's plan card says and what its
prefill button does. Both should be added to the asks before the next batch goes out.

**Still unswept:** Profile / Terms (image10), Approvals detail (image2), the Targeted and Premium
Launch variants (image5), Inventory's rate-management and availability screens (image6), and the
Campaign Manager variants (image12–15).

---

## Round 9 — sweep: Campaign Approvals, Targeted wizard

### R21 ✅ RESOLVED — maker–checker, and it is stated outright

Campaign Approvals subtitle: *"**Maker–checker** · review and authorise campaigns submitted by your
campaign managers."* And the explanatory banner:

> *"Campaigns created by your **managers** stay in **Pending** until **you** authorise them. Approving
> requires **OTP** verification — select one or many and approve collectively. Approved campaigns
> schedule to their flight dates; rejected ones return to the manager with your remark."*

So the three endings reconcile exactly as hypothesised, by **role**:

| Role | Terminal action |
|---|---|
| `AD_PARTNER_ADMIN` | **Activate** — they are the checker, so their own campaign needs no approval |
| `CAMPAIGN_MANAGER` | **Submit for approval** → Pending → admin authorises with OTP |

**Genie requirement (G8): the prefill block's CTA label is computed from the user's role**, exactly
like the opener. An admin sees "Prefill and activate"; a campaign manager sees "Prefill and submit
for approval". Getting this wrong tells a user their campaign is live when it is queued.

Also captured: rejection returns the campaign to the manager **with a remark** — so there is a
reviewer-comment field Genie should be able to read back.

---

### The Targeted flow is a different product, not a variant

Targeted step 2 is **"Audience Targeting"**, not "Select Screens", and it opens with:

> *"**No screens to reserve** — the system serves your 10-sec ad into an empty slot the moment a
> matching person is detected on any device."*

| | Broadcasting | Targeted |
|---|---|---|
| Step 2 | Select Screens | Audience Targeting |
| Unit bought | reserved screens, city × store category | **won impressions** in an auction |
| Price | slot rates | **second-price auction** above a floor |
| Output shape | screens · city allocation | bid · budget · matched reach/day |

**This changes S6.** The Planner needs **two models**, not one with a flag — Broadcasting is an
allocation problem, Targeted is a bidding problem. They share almost nothing.

### R29 🆕 — AdGuide's plan card has no Targeted variant

The Calculator form offers **Campaign type: Broadcasting | Targeted**, but the "Recommended plan"
card is entirely screen-reservation shaped: *Screens 1,818 · City allocation · Delhi 516…*

For Targeted there are **no screens to reserve**. The card would need to show floor CPM, suggested
bid, matched reach/day and estimated spend instead. **The design does not have that card.**

Either the Calculator is Broadcasting-only in v1 (fine — say so and grey the toggle), or a Targeted
plan card needs designing.

---

### Free win: the Targeted pricing model is fully specified

The "Auction & Delivery" panel gives the whole CPM build-up:

```
Base                      ₹90
  × Audience   1.3   (Standard)
  × Context    1     (Standard)
  × Format     1.2   (Both)
  × Day-part   1.19  (3 Slots)
  = Floor CPM  ₹167   · per 1,000 verified
```

Plus the mechanics, verbatim:

- *"Suggested ₹220 to win competitively · you clear at ₹200 (**second-price**)"*
- *"You're charged per **won, camera-verified matched impression** at the clearing CPM (≥ floor)"*
- *"Spend never exceeds your budget or daily limit — and can't exceed available **matched supply**"*
- *"narrower audiences cost more per impression but reach the exact person — the auction clears above
  the floor only when demand competes"*

**Day-part is a pricing multiplier (×1.19 for 3 slots)** — dayparts are commercially load-bearing,
which further supports R2's booking-definition ruling.

### Free win: pacing is a user setting, not just an advisor concept

Targeted campaigns choose a **Pacing** mode:

| Mode | Behaviour |
|---|---|
| **Even (1x)** | *"Smooth — equal spend across all days & through the day. Always-on, predictable."* |
| **Accelerated (2X)** | *"Fast — spends as matches appear until budget runs out. Front-loads delivery."* |

**This changes the `budget_pacing` advisor (S9).** "Behind schedule" is a defect under Even and the
*expected* behaviour under Accelerated. The advisor must read the campaign's pacing mode before
deciding anything is wrong — otherwise it will cry wolf on every accelerated campaign.

### Free win: two more advisors specified by the design

| Advisor | Spec found |
|---|---|
| `approval_backlog` | Approvals KPI: **"Oldest Waiting · 3 days · approve promptly to hold flight dates"** — including why it matters (flight dates slip) |
| `subwallet_short` | Approval row: **"Sub-wallet sufficient — Amit Desai · balance ₹9.00 L vs budget ₹4.60 L"**, and the KPI *"Budget Awaiting Approval · to be drawn from **sub-wallets**"* |

---

## New conflicts

### R26 🆕 — a campaign objective that is not in the objective list

The approvals detail shows **Objective: Footfall**. Launch step 1 offers six objectives: Brand
Awareness · Product Launch · Seasonal Promotion · Tactical Campaign · Regional Campaign · Test &
Learn. **"Footfall" is not among them.**

### R27 🆕 — named audience segments exist on one screen only

The approvals detail shows **Audience segments: "Youth 18–28" · "Café & hangout visitors"** — named
cohorts. The Targeted wizard has no segment concept at all; it targets by raw Gender × Age group ×
Store category context.

Note "Youth 18–28" matches **neither** age taxonomy (backend 18-25/26-35/36-50/50+, design
18-24/25-34/35-44/45-54/55+). Either named segments are a separate unbuilt feature, or the approvals
screen is aspirational.

### R28 🆕 — a third daypart representation

The approvals detail shows **Day-part: 11:00 – 22:00** — a continuous range, not the four named
bands. And it starts at **11:00** where every other screen starts the sellable day at **10:00**.

Possibly a Targeted-only representation (continuous windows rather than discrete slots), but that is
not stated, and the Targeted wizard's own left rail still shows the four named chips.

### R15 ⬆ REFINEMENT — reporting bands and targeting bands may legitimately differ

The Targeted wizard targets on **18-24 · 25-34 · 35-44 · 45-54 · 55+** — the five-band design
taxonomy, used consistently across Command Centre, campaign detail and here.

The backend **reports** four opaque buckets, and the contract says render what the API returns.

These may not be the same thing: **targeting bands are a selection input to the ad server; reporting
bands come from the analytics rollup.** R15 should be asked as two questions rather than one —
"which bands do we report in" and "which bands can a buyer target on", with a note that a mismatch
between them is itself confusing to a user who targets 25-34 and then reads a report bucketed 26-35.

---

## Structural facts

- **Step 2 differs by motion**: Broadcasting → Select Screens; Targeted → Audience Targeting. Steps 1,
  3, 4 are shared. Prefill must be motion-aware.
- **`REQ-2608-01`** — approval requests carry their own id, distinct from the campaign id (`#625427`).
  A separate entity for the ontology.
- **EMPLOYEE / AGENCY badges** appear on every approval row, consistent with the Team Management
  Add-User distinction. The concept is real and used, though it has no `PlatformRole` equivalent.
- **INVENTORY (STANDARD / PREMIUM) is a first-class campaign attribute**, shown as a table column on
  approvals — relevant to R14.
- Approvals live under **Notifications → Pending Approvals**, a sub-nav, not a top-level tab.
- Store categories are **Grocery · Pharmacy · Electronics · Dairy · Clothing · Others**, with
  Pharmacy and Electronics flagged **high-intent (×1.30)**. Confirms "Clothings" on Select Screens is
  the typo.

---

## Running tally after round 9

| | |
|---|---|
| Resolved or dropped | **10** — R1 · R2 · R3 · R7 · R9 · R10 · R11 · R12 · R15 · R21 |
| Open, design decisions | **16** — R4 · R5 · R6 · R8 · R13 · R14 · R16 · R17 · R18 · R19 · R24 · R25 · R26 · R27 · R28 · R29 |
| Open, backend verification | **2** — R20 · R22 |

R22 is partly informed: manager-created campaigns draw from **sub-wallets**, while the Settlement
Notice describes rental campaigns needing no payment at all. Both mechanisms exist; the ruling is
about which applies when.

**Still unswept:** Profile / Terms (image10), the Premium Launch variant (image5), Inventory's
rate-management and availability screens (image6), and the Campaign Manager variants (image12–15).

---

## Round 10 — `CAMPAIGN_CREATION_GUIDE.md` (2026-09-17, branch `dev-ayush-1.33`)

The single most valuable document received. It closes **six** open conflicts outright, upgrades three
from "recommended" to "definitive with values", and **corrects an error I had put into
`asks/BACKEND-CHECKS.md`**.

---

### ⚠️ Correction to my own earlier conclusion — GST

I wrote in `BACKEND-CHECKS.md`: *"the wallet is debited on the AdGrid→AdPartner leg, **ex-GST**; GST
applies on the AdPartner→Brand leg."* **That is backwards.**

§10.1 and §10.4:

```
leg 1: Adgrid → AP    contract rate × plays, + 18% GST   = campaign.adgrid_cost
leg 2: AP → Brand     AP→Brand rate card × plays, NO GST = campaign.total_cost
```

- Coverage check at initiate-confirm: **`balance ≥ adgridCost × 1.18`**
- Debit at OTP verify: **`Dr wallet (base + GST)`** / Cr deferred revenue (base) / Cr GST payable (GST)

So the `adgrid_cost` **field** is ex-GST, but the **wallet debit is base × 1.18**. GST sits on leg 1,
not leg 2. My reading of the proto comment was right about the field and wrong about the debit.

**`BACKEND-CHECKS.md` has been corrected.** Anyone who already received it should be told.

---

### R2 ✅ DEFINITIVE — daypart boundaries confirmed with values

- Key rule 3: *"A daypart stays bookable until it ends. For example, today's **MORNING (10–13)** can
  still be booked at 11:00."*
- Vocabulary: *"loops = daypart seconds ÷ loop seconds. **A 3 h daypart** on a 60 s loop gives
  10,800 ÷ 60 = 180."*
- `TimeSlots {MORNING, AFTERNOON, EVENING, NIGHT}`, **each with start/end**

So: **MORNING 10–13 · AFTERNOON 13–16 · EVENING 16–19 · NIGHT 19–22**, four 3-hour bands, a
**10:00–22:00** sellable day. Exactly what the campaign detail screen shows.

**The Command Centre's "Daypart Occupancy" (6a–12p / 12–5p / 5–10p / 10p–6a) is simply wrong** and
should be re-bucketed. No decision needed — it is a defect.

### R3 ✅ DEFINITIVE — and both annotations were right, for different tiers

> `program`. Seeds: **`PROGRAM_120`** (premium, **120 s** loop, has promo strip) and **`PROGRAM_60`**
> (non-premium, **60 s** loop, no strip).

- The Avg Dwell card's *"on a 120s loop"* describes **Premium**.
- The Launch wizard's *"a 60-second loop holds 6 static"* describes **Non-premium / Standard**.

**Both are correct and both are missing the tier qualifier.** Better than my round-1 answer
("per-cell, neither is right") — it is per **tier**, via `program`, and `loops_per_time_slot` derives
from it.

### R17 ✅ RESOLVED — one canonical state machine

```
DRAFT ──OTP verify──► SCHEDULED ──hourly job──► LIVE ──hourly job──► COMPLETED
                          └──────── cancel (OTP) ────────┴──► CANCELLED
```

Terminal: COMPLETED, CANCELLED. A DRAFT is never cancelled — it just stays a draft.

**Effective status** (computed for the UI, never stored): **PAUSED** (campaign paused),
**NOT_PLAYING** (client suspended — this wins over paused).

Mapping the nine words found in the deck:

| Design word | Reality |
|---|---|
| **Live** | ✅ correct — `LIVE` |
| **Scheduled** | ✅ correct — `SCHEDULED` |
| **paused** | ✅ correct — effective status `PAUSED` |
| **Active** (Recent Campaigns) | ❌ should be **LIVE** |
| **Broadcasted** | ❌ **not a status at all** — it is `deliveryMode = BROADCAST` |
| Pending · Approved · Rejected | a different machine — the **approval request**, not the campaign |
| Draft | ✅ `DRAFT` |

My hypothesis on "Broadcasted" is confirmed: a delivery mode sitting in the status row.

### R20 ✅ RESOLVED — the tier/motion permission gate does not exist

§2.3 lists every role's campaign permissions, and they are **all per-operation**:

| Role | Permissions |
|---|---|
| `ADOPS_ADMIN`, `SUPER_ADMIN` | everything; also see Adgrid's cost line in price previews |
| `AD_PARTNER_ADMIN`, `NETWORK_PARTNER_ADMIN` | everything for their own client |
| `CAMPAIGN_MANAGER` | view, create, edit, launch (confirm), pause. **Cannot cancel** — lacks `DELETE_CAMPAIGN` |
| `BRAND_MANAGER` | **view only** |

**There is no per-user tier dimension and no per-user motion dimension.** What actually constrains
tier and mode is:

- the factory keyed on **(ClientType, deliveryMode)** — AP may do BROADCAST and TARGETED; NP only BROADCAST
- **client-level** holdings: *"A campaign can only use slots its client already holds"* — via
  `client_batch_allocation`

So the Add-User form's **"Inventory Access"** and **"Campaign Access"** tick-boxes have **no backend
equivalent**. They are UI-only, or unbuilt. Confirmed as I suspected, without needing to test.

Two roles also surface that are not in the `PlatformRole` enum I read earlier: **`SUPER_ADMIN`** and
**`MARKETING_AGENCY_ADMIN`** — the latter maps neatly onto the form's *"Marketing Agency"* option, so
the Employee/Agency toggle does correspond to real roles.

### R22 ✅ RESOLVED — when money moves, exactly

| Client / mode | At confirm |
|---|---|
| **AP broadcast** | Both legs priced. **Wallet pays leg 1 + 18% GST.** Leg 2 recorded as receivable from the brand |
| **AP targeted** | **Postpaid.** Nothing charged at confirm; floor CPMs recorded for later |
| **NP** | **Cost 0.** Flat contract, billed outside campaigns. No wallet |

Money flow: coverage check at initiate-confirm (no movement) → **debit at OTP verify** → revenue
recognised at completion. Wallets are a double-entry ledger: agency `WALLET:CLIENT:{apId}`, member
`SUB_WALLET:USER:{userId}`.

**Payer:** the campaign's **creator**, from their sub-wallet, if their role has one
(`CAMPAIGN_MANAGER`, `MARKETING_AGENCY_ADMIN`). Otherwise the agency wallet. *"It is never the
approver or the person who clicked confirm."*

**Two consequences for AdGuide:**

1. **The plan card's "Reserves ₹X from ₹Y" is correct only for AP Broadcast** — and must show
   **base × 1.18**, not base. For **Targeted it is wrong entirely** (postpaid, nothing reserved).
2. **The Settlement Notice on Launch step 4** — *"settled against your existing screen rental
   agreement, no additional payment to AdGrid is required"* — describes the **NP** case. This is the
   AD_PARTNER dashboard, where broadcast campaigns **do** debit the wallet. **That notice looks
   wrong on this screen.** New defect, not a ruling.

### R24 ✅ RESOLVED — pending creatives cannot be used

- Key rule 8: *"**Every requested slot needs one approved ad**, per zone, before confirm."*
- §12.4: *"Attach refuses a non-usable ad"* and *"Sub-status counts usable ads only, so the campaign
  **cannot reach CREATIVES_COMPLETE and cannot be confirmed**."*

**The Creative Library banner is right. The Launch step 4 tip — *"You can activate now even with
pending creatives — they'll go live once approved"* — is wrong.** A defect.

Creative states confirmed: `review_status` = **AWAITING_AI_VERDICT · APPROVED · PENDING_REVIEW ·
REJECTED**. The Creative Library shows three of these and omits AWAITING_AI_VERDICT.

Also: *"The AI never rejects an ad. It either approves it or sends it to a person. If it says nothing
within 5 minutes, a person decides."*

### R27 ✅ RESOLVED — named audience segments do not exist

§13: targeted create takes `targets[] {city?, shopType?}` and
`targeting {ageGroups, genders, imageCpm, videoCpm, totalBudget, dailySpendLimit, pacing}`.
Topics are `adgrid/{clientId}/client/all`, `…/{city}/city`, `…/{shopType}/category`,
`…/{city}/city/{shopType}/category`.

**Targeting is city × shopType × ageGroups × genders. There is no named-segment concept.** The
approvals screen's *"Youth 18–28"* and *"Café & hangout visitors"* have no backend equivalent.

### R28 ✅ PROBABLY RESOLVED — targeted campaigns have no dayparts

Targeted books **no slots at all** — *"No bookings, no hold, no price"*. So a *"Day-part: 11:00 –
22:00"* line on a Targeted approval row has nothing behind it. Same class as R27.

### R26 ✅ RESOLVED — "objective" is not a backend field

Searching the guide for "objective" returns **nothing**. Campaign objective is a **UI-only concept**,
so both the wizard's six options and the approvals screen's "Footfall" are design's to define. The
only fix needed is making the two lists agree.

### R25 ⬆ STRONG EVIDENCE for portrait

The AI-checker integration contract (§12.5) carries
`"detail": { "width": 1080, "height": 1920, … }`.

Combined with the Live Preview rendering a tall portrait screen, **1080 × 1920 is almost certainly
correct and the Launch wizard's 1920 × 1080 is the error.** Still worth one confirmation, but it is
now a defect report rather than an open question.

### R11 ⬆ the canonical list is an enum

`CityCode {DEL, GGN, NOI, MUM, BLR, HYD, CHN, PUN}` — **eight** codes, including Mumbai, Bangalore,
Hyderabad, Chennai and Pune. Every list in the deck is a subset.

**`GGN` is the code; the display name is design's choice** — which narrows R11 from "which list" to
"what do we render for each code". Same for `ShopTypes {GROCERY, PHARMACY, DAIRY, ELECTRONIC,
CLOTHING, OTHERS}` — the design's "Electronics" and "Clothings" are display variants of `ELECTRONIC`
and `CLOTHING`.

---

## Free wins for the build

| | Finding | Affects |
|---|---|---|
| **Broadcast is not competitive** | *"Clients never compete for the same slots. Each client's share of a screen is fixed when its contract starts."* The allocator picks within slots the client already holds — no auction, no contention | **S6 — simplifies the allocator substantially** |
| **Hold TTL is 10 minutes, OTP 2 minutes** | *"A hold lasts 10 minutes, and the confirm OTP lasts 2 minutes. Resending the OTP keeps the same hold."* | Answers `BACKEND-CHECKS` §4 |
| **A draft holds nothing** | Screens are held only at confirm — so a plan can sit unconfirmed without consuming inventory | S8 prefill |
| **Zones** | `ZoneSections {HERO, PROMO_STRIP, SHOWCASE}` — three. Note analytics also mentions `endcap`; possible mismatch worth a look | ontology |
| **Endpoint map** | §6.0 lists the creation journey in call order | S1 `source.api` fields |

---

## Status after round 10

### Resolved — 17

R1 · R2 · R3 · R7 · R9 · R10 · R11 · R12 · R15 · R17 · R20 · R21 · R22 · R24 · R26 · R27 · R28

### Open — 9, and **all nine are design decisions**

| | Ruling | Note |
|---|---|---|
| R4 | Which "Wallet Balance" | Now precise: agency wallet, sub-wallets, or the sum. Two ledger types exist |
| R5 | Nav label vs page heading | Convention |
| R6 | CM sees Top up / Settle | CM has no wallet permission and pays from a sub-wallet — supports "alert only" |
| R8 | Six screen terms | Backend term is `device` / `device_inventory` |
| R13 | "Active Campaigns" definition | Largely folds into R17 — "Active" should read LIVE |
| R14 | Premium vs Standard on the Command Centre | Tier is fixed per campaign; the CC has no toggle |
| R16 | Three "verified" terms | Naming |
| R18 | Four names for impressions | Naming |
| R19 | Brand Type vs Industry | Naming |

### Defects found, not rulings — 4

| | What |
|---|---|
| 1 | Command Centre "Daypart Occupancy" uses the wrong hours |
| 2 | Launch step 4 tip says pending creatives can be activated — they cannot |
| 3 | Launch step 4 Settlement Notice describes the NP case on an AD_PARTNER screen |
| 4 | Launch step 1 creative dimensions are almost certainly transposed |

### AdGuide design gaps — 2

| | What |
|---|---|
| R29 | No Targeted variant of the Recommended Plan card |
| — | The plan card's reservation line must be base × 1.18, and must not appear at all for Targeted |
