# Resolution policy — code and API win over design

Adopted 2026-09-24, on the product owner's instruction: *"give priority to what you already know over
Figma, because the designer has committed many mistakes — getting stuck every time we encounter one
will take too much time."*

---

## The rule

When the designs and the running system disagree, **the running system is the answer.** Record the
divergence as a design defect, adopt the backend's meaning, and keep moving.

A conflict only blocks Genie when **neither** source has an answer — not when they merely disagree.

### Precedence

| | Source | Weight |
|---|---|---|
| 1 | **The live API** — `reference/bff-openapi.json`, response schemas, field names | Highest. This is what actually returns |
| 2 | **Backend source and contracts** — enums, `@RequireAccess`, `CAMPAIGN_CREATION_GUIDE.md`, contract v3.1 | Authoritative for semantics the API does not spell out |
| 3 | **Product owner rulings** | Override 1 and 2 where they are product choices rather than facts |
| 4 | **The Figma deck** | Lowest. Useful for intent and vocabulary, unreliable for values, units and gates |

### What changes in practice

- A metric whose name differs between screens is **resolved to the API's field name**, not left blocked.
- A definition that differs between screens is **resolved to the backend's**, with the design noted
  as a defect.
- `status: blocked` is now reserved for **"no source exists anywhere"** — not "two screens disagree".
- Every resolution made under this policy is tagged **`POLICY 2026-09-24`** in the ontology, so it is
  trivially findable and reversible if someone disagrees.

### What this does not change

- **Nothing about safety.** Genie still never writes, still refuses OTP-gated operations, still
  states provenance.
- **Suppression still means suppressed**, never zero.
- Where a wrong answer would cost the user real money or a rejected upload, Genie still asks or
  declines rather than guessing.

---

## Resolutions adopted under this policy

### R12 · "Reach" — four concepts, each named for what it is

The platform computes four different things. Genie reports whichever the endpoint returns, always
with its qualifier, and never presents one as another.

| Field | What it is | Genie's label |
|---|---|---|
| `AudienceWindow.uniqueVisitors` | exact distinct persons over the window, from `person_day` | **Unique reach** — owner-scoped, so not available to an agency |
| `ContentWindow.reach` | max single-day distinct persons | **Reach (best day)** |
| `CampaignMetrics.dailyReach` | footfall per day — *not persons* | **Daily footfall** |
| `estimatedMonthlyReach` | `dailyReach × days`, no de-duplication | **Gross monthly reach** |

Only the first is window-unique, and it is unavailable to ad partners — which is why S5 exists.

### R8 · "Screen" — four concepts, the other two retired

| Concept | Source | Retires |
|---|---|---|
| **Contracted screens** | contract / batch allocation | "Owned Screens", "Total Screens" |
| **Live screens** | device status | "LIVE SCREENS", "Active Screens" |
| **Targeting-capable screens** | camera + CV present | (kept) |
| **Screens in use** | `totalScreens` on a campaign | "Screen Used" |

Premium vs Rental is a **commercial model**, not a screen count. Never a headline metric.

### R4 · "Wallet Balance" — the main wallet

`GET /clients/{clientId}/wallet/balance` → `balance`. The Command Centre card means this one.
Sub-wallet total comes from `/members/wallet-overview`; "total credits" is a UI aggregate over both
and is never a ledger.

The runway advisor is unaffected either way — the backend computes `runwayMonths` and `lowBalance`.

### R15 · Age bands — reporting and targeting are different things

- **Reporting**: four opaque keys from the API, rendered exactly as returned. Never parsed, never
  re-bucketed. `50+` holds 51+, and is being relabelled `51+`.
- **Targeting**: the five design bands, because they are an input to the ad server rather than a
  read of the rollup.

These may legitimately differ. Genie says which it is showing.

### R16 · "Verified" — three distinct things

| Term | What it is | Unit |
|---|---|---|
| **Verified impressions** | share of served impressions a camera confirmed a real person saw | percent |
| **Verified viewers** | count of people so confirmed | count |
| **Attention** | `avgAttention` — share of people present who looked | 0–1 ratio |

A share and a count are not interchangeable. Genie names the unit every time.

### R18 · Impressions — one name

**"Impressions"**, matching the API's `impressions` / `totalDailyImpressions`. The variants
"Impressions Delivered", "Total Daily Impressions" and "Daily Impression" all mean this.

Impressions are **playback events, not people**.

### R14 · Command Centre tier scope — blended

The Command Centre has no Premium/Standard toggle, so its figures are treated as **blended across
tiers** and labelled as such. Tier is fixed per campaign, so anything campaign-scoped carries its own
tier.

---

## Still genuinely blocked — no source anywhere

These are not disagreements. Nothing returns them.

| | What | Why |
|---|---|---|
| **Campaign delivery metrics** | impressions, reach, spend-to-date, pacing, per-city breakdown | `CampaignDetailResponse` carries configuration only; `CampaignMetricsResponse` returns just budget and totalScreens. Delivery lives on `/dashboard/content`, which is `client_id`-scoped — the R7 route |
| **Camera/audience metrics for an agency** | demographics, hour rhythm | `ContentWindowResponse` still has no `genderSplit`, `ageDistribution` or `hourRhythm` |
| **Window-unique reach for non-owned screens** | the plan card's headline | Needs S5's frequency model |
| **R29** | a Targeted variant of the plan card | An AdGuide design gap, not a data gap |

**This is the honest shape of the gap: the BFF serves configuration and commerce well, and delivery
measurement for ad partners not at all.**
