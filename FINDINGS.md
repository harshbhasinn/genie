# Design Intake — Findings

Read of `Agency Admin Dashboard (1.5).docx` — 15 Figma frames, 2 role variants.
Date: 2026-09-17. **Companion to `PLAN.md` and `RAG.md`. Where they disagree, this one wins.**

---

## 0. Headline

**The design already contains most of Genie.** It is called **AdGuide**, it has screen context, a
provenance footer, a proactive opener, a deterministic Calculator tab, and a plan → prefill →
approval handoff. That is a better action model than the one I proposed in `PLAN.md` §8, and it
should replace it.

Three things change materially:

1. **The hard part is not the LLM — it is the Calculator** (§4). A media-planning engine that turns
   budget + duration + mix into screens, impressions, reach, CPM and a city allocation. Deterministic,
   and the largest single component in the build.
2. **Genie never executes. It prefills.** The Launch wizard is the confirmation step, and approval
   needs an OTP sent to the admin's mobile plus a ToS acknowledgement. Most of `PLAN.md` §8's
   confirmation state machine is unnecessary (§5).
3. **Eight contradictions in the designs must be resolved before the ontology can be built** (§6).
   Two of them are the exact "one label, two meanings" failure `RAG.md` §2.1 predicted — and they
   are real, in your product, today.

---

## 1. Screen inventory

| # | Screen | Agency Admin | Campaign Manager |
|---|---|:---:|:---:|
| 1 | Home / Command Centre | ✅ | ✅ |
| 2 | Notifications & Approvals | ✅ | ❌ |
| 3 | Brand Management | ✅ | ✅ |
| 4 | Creative Library | ✅ | ✅ |
| 5 | Launch Campaign (3 wizard variants) | ✅ | ✅ |
| 6 | Inventory Management | ✅ | ✅ |
| 7 | AdGuide / Genie | ✅ | ✅ |
| 8 | Wallet Management | ✅ | ❌ |
| 9 | Team Management | ✅ | ❌ |
| 10 | Profile · Terms and Policies | ✅ | ❌ |
| 11 | Brand Dashboard | — | ✅ |

**Nav, Agency Admin:** Command Centre · Brand Management · Inventory Management · Wallet Management · Team Management
**Nav, Campaign Manager:** identical minus **Team Management**.

Tenant in the mocks: *Infinity Advertising* (an `AD_PARTNER`), Delhi NCR, 50,000 screens, 6 cities,
3 contracts. Brands beneath it: Coca-Cola, Samsung, Airtel, Titan, boAt, H&M, Vicks, Dettol, Pepsi.
Campaign managers: Akshay Khanna, Priya Sharma, Amit Singh.

---

## 2. The gift: metric definitions already exist

Every frame carries red-boxed annotation cards, one per metric, in a fixed shape:

```
  <Metric name>
  <One-sentence plain-language definition>
  HOW IT'S CALCULATED
  <formula, with the worked number>
```

Verbatim examples:

| Metric | Definition | How it's calculated |
|---|---|---|
| Network Reach | Unique people the network can put an ad in front of per day — camera-counted footfall, not an estimate. | `Σ (footfall per screen/day), de-duplicated = 15.0M/day ≈ 300 footfall/screen/day × 50,000 screens` |
| Avg Attention | Of the people present at a screen, the share who actually turned to look at it. | `faces looking ÷ faces present (on-device CV) = 70%` |
| Avg Dwell | How long, on average, a viewer stays within view of a screen. | `Σ dwell seconds ÷ viewers = 75s → on a 120s loop, a viewer can catch ~1–2 spots` |
| Verified Impressions | Share of served impressions the camera confirmed a real, present person actually saw — proof-of-attention, not just proof-of-play. | `confirmed-seen ÷ served = 89%` |
| Blended yield / 1,000 | What the network earns per 1,000 impressions across both motions combined. | `total revenue ÷ total impressions × 1,000 ≈ ₹116` |
| Wallet Balance · Rev-Share | Prepaid credit that funds revenue-share consumption as campaigns run. | `top-ups − consumption = ₹15,75,000; runway = balance ÷ ₹2.6L/day burn ≈ 6 days` |
| Gross Profit | What the agency keeps after AdGrid's platform cost. | `₹2.76 Cr − ₹1.19 Cr platform cost = ₹1.57 Cr; margin = 56.9%` |
| Platform Invoice | What AdGrid bills the agency this cycle — revenue-share plus screen rental. | `RevShare consumption + screen rental = ₹42.0L` |

**This is the Dashboard Ontology, pre-written by your design team.** `PLAN.md` §3 assumed we would
author it from screenshots; instead we transcribe it. Phase 1 shrinks substantially.

**And it has a second consumer.** Every metric card in the UI carries an **(i) icon**. That tooltip
and Genie should read the *same* definition store — one source, two surfaces. This is the argument
that gets the FE team to care about the ontology: it is not Genie overhead, it is their tooltip content.

⚠️ These are **design-time specifications**. `RAG.md` §2.2's rule stands: every one must be verified
against the code that computes it before Genie repeats it. §6 shows why that is not a formality.

---

## 3. AdGuide as designed

```
┌─────────────────────────────────────────┐
│ ✦ AdGuide                            ✕ │   "Your dashboard copilot"
│ [ Ask AdGuide ]  [ Calculator ]         │   two tabs
├─────────────────────────────────────────┤
│ ● Viewing  Home · Overview              │   ← ui_context, rendered
├─────────────────────────────────────────┤
│                                         │
│  "You're on your Home overview. Two     │   proactive opener,
│   things stand out: 7 campaigns are     │   specific and numeric
│   waiting for your approval, and        │
│   ₹1.90 Cr of partner billing is due    │
│   01 Jun. Want me to walk through       │
│   either — or plan something new?"      │
│                                         │
│  [What needs my attention?]             │   suggestion chips
│  [Explain my wallet position]           │
│  [Plan a new campaign]                  │
│                                         │
├─────────────────────────────────────────┤
│ Ask about this screen, marketing, or…▸ │
├─────────────────────────────────────────┤
│ ⓘ Grounded on what's on your screen ·   │   ← provenance + posture
│   planning on AlphaRule's allocation    │
│   (22,160 screens, 6 NCR cities).       │
│   AdGuide advises — you decide.         │
└─────────────────────────────────────────┘
```

Four design decisions here are exactly right and should be treated as locked requirements:

- **`Viewing Home · Overview`** — the panel *shows* the context it is grounded on. Screen awareness
  is visible, not implicit. (`PLAN.md` §7.)
- **The footer states its grounding and its scope** — "on your screen", "AlphaRule's allocation
  (22,160 screens, 6 NCR cities)". This is the provenance rule from `PLAN.md` §10, at panel level.
- **"AdGuide advises — you decide."** The advisory posture, stated to the user.
- **The opener is specific**, not "How can I help?" — it names counts, amounts and a date.

A worked answer from the deck, which is the Advisor output format almost exactly:

> **What needs my attention?**
> "Three things, in order: **1)** 7 campaigns pending approval — the oldest has waited 3 days, and
> IPL Fan Zone is flagged sub-wallet-short by ₹6 L. **2)** ₹1.90 Cr partner billing due 01 Jun.
> **3)** Your 8 live campaigns are all pacing to plan — nothing to act on there."

Ranked, numeric, and it reports a *negative* finding rather than padding. Item 3 is what stops the
feature becoming noise.

---

## 4. The Calculator is the core, and it is not an LLM

The **Calculator** tab is a deterministic form, and its output is the product.

**Inputs:** Campaign type (Broadcasting = "Reach play — SOV across footfall, priced on CPM" |
Targeted) · Objective · Total budget (from the wallet) · Target reach (optional; *blank = maximise*) ·
Campaign length (7 / 15 / 30 day chips) · Fine-tune: Creative (Image·10s | Video·15s), Time slots,
Inventory mix (Premium-heavy | Balanced | Standard-heavy).

**Output — "Recommended plan":**

```
Broadcasting · Product Launch · 30 days
Campaign cost      ₹2.50 L of ₹2.50 L     ████████████
Reach vs target    8.3 L / 4.0 L          ████████████
Screens 1,818   Impressions 24.8 L   Blended CPM ₹101   Daily burn ₹8k/day
City allocation  Delhi 516 · Gurugram 377 · Noida 352 · Faridabad 229 · Ghaziabad 194 · Other NCR 150
💳 Reserves ₹2.50 L from ₹12.30 L · ₹9.80 L left
✓ Target met — 107% headroom
  • Reaches 8.3 L unique people vs your 4.0 L target.
  • Spend the headroom by weighting Evening or adding Premium venues.
[ Refine in Calculator ]  [ ✦ Prefill wizard → ]
```

**This is a media-planning engine.** To produce those numbers it needs:

| Input | Where from | Status |
|---|---|---|
| Screen inventory by city, tier, availability | Inventory service | **Unknown — §7** |
| Rate cards (Premium ₹110 / Standard ₹80; targeted floor ₹167 + ~20% uplift) | Billing / rate-card service | Partly exists (`PremiumRateCardController`, `ClientRateCardController`) |
| Loop math (spots/min, creative length) | Constant — **but disputed, §6.3** | ⚠️ |
| Footfall per screen/day + reach de-duplication | Analytics (camera) | Exists in `dashboard_views` |
| Daypart availability | Analytics | Exists (`hour_counts[24]`) |
| Wallet balance + reservation | Wallet service | Exists |

**Consequences for the plan:**

- `PLAN.md` §9 said "recommendations are computed by deterministic code; the LLM only explains them."
  The design agrees, and goes further: the calculation has its own tab and its own UI. **Rename the
  component: the `advisor/` module becomes `planner/` (the engine) + `advisors/` (the watchers).**
- The numeric post-check on narration stays exactly as specified, and now has an obvious anchor: the
  narrator may only use numbers present in the plan object.
- **This is the long pole of the build**, not the chat. Budget it accordingly, and get the model
  reviewed by whoever prices inventory before writing code.
- "Blank = maximise" means the engine must support both *constrained* (hit this reach) and
  *unconstrained* (maximise reach within budget) modes. Different optimisations.

---

## 5. The action model: prefill, never execute

The deck's flow:

```
AdGuide Calculator → Recommended plan → [Prefill wizard] → Launch wizard (fully editable)
    → [Submit for approval] → Approvals queue → [Authorise] → OTP + acknowledgement → live
```

**"Prefill the Launch wizard"** modal: *"Every plan value maps to a wizard field — review, then
submit for approval."* and *"Prefilled by AdGuide — 1,818 screens · 30 days · ₹2.50 L. **Everything
stays editable.**"* It renders a resolved summary (campaign name, type, objective, schedule,
creative, inventory tier mix, cities, screens·freq, planned cost) then Cancel | **Submit for approval**.

**The approval gate is a hard human boundary.** From the Approvals screen:

> **Authorise selected campaigns** — *"Both an OTP and your acknowledgement are required for this action."*
> Campaigns 3 · Total Budget ₹28.60 L · AdGrid Platform Charges ₹12 L
> *"A 6-digit OTP has been sent to the admin's registered mobile +91 ••••••3210."*
> ☐ *"I confirm I have reviewed all the selected campaign and, on behalf of the account, accept the
> Terms of Service and Campaign Policy that apply."*
> [Cancel] [Verify & Approve] · error state: *"Invalid OTP. Please try again."*

This is AdGrid's MPIN equivalent, and the backend already has `otp-service` (:9092) and
`OtpController`.

### What this changes in `PLAN.md` §8

| `PLAN.md` said | Replace with |
|---|---|
| Capability catalog of ~40–80 executable operations | Mostly **`prefill.*`** and **`navigate.*`** capabilities, plus a short list of genuinely low-risk direct actions |
| Confirmation state machine (COLLECTING → AWAITING_CONFIRMATION → EXECUTING) | **The wizard is the confirmation.** Keep the machine only for the few direct actions |
| `blast_radius: financial` requires typed confirmation | **Genie cannot perform financial actions at all.** OTP + acknowledgement is a human gate by design |
| v1 mutation set: pause / resume / edit budget / upload creative / promo banner | Re-scope against §7 once we know what exists |

**This is a better design than mine and it is safer.** Genie proposes; a human reviews an editable
form; a second human authorises with an OTP. Three gates, none of which Genie can pass on its own.
It should be stated as a product principle, not just an implementation detail.

Two-tier wallet, from the Wallet screen: **agency main wallet → campaign-manager sub-wallets**
(Akshay ₹40L allocated / ₹25.5L spent, Priya ₹25L/₹16.2L, Amit ₹18L/₹12L). Top-up is *"Request funds
from AdGrid"* — a request, not self-service. Genie can draft a request; it cannot move money.

---

## 6. Contradictions found — these block the ontology

Eight, in severity order. Each needs a ruling before the metric can be modelled.

### 6.1 "Avg Attention" means two different things — SEVERE

| Where | Value | Meaning |
|---|---|---|
| Command Centre annotation | **70 %** | `faces looking ÷ faces present` — a ratio |
| `Java_Backend/content-performance/definitions.md` | **4.8 s** | derived from `dwell_sum_ms / dwell_n` — a duration |

Same label, two units, two screens. That document flags it as unresolved ("**This is decision #1**")
and warns that the codebase ships `attn_sum/attn_n` (0–1 score) *and* `dwell_sum_ms/dwell_n`
(seconds) side by side.

This is precisely the failure `RAG.md` §2.1 predicted, and worse than the `budget`/`batch`
utilization case because the label is *identical*. A user asking "what's my average attention?" must
get a different answer on two screens. **Ruling needed: does "Attention" mean the ratio or the
duration? The other one gets renamed.**

### 6.2 Daypart definitions conflict — SEVERE

| Label | Command Centre · "Daypart Occupancy" | Launch wizard · "Time Slots" |
|---|---|---|
| Morning | 6a–12p | 10AM–1PM |
| Afternoon | 12–5p | 1PM–4PM |
| Evening | 5–10p | 4PM–7PM |
| Night | 10p–6a | 7PM–10PM |

Four identical labels, four different ranges, and the Command Centre set covers 24h while the wizard
set covers 12h. The Command Centre insight *"Night only 39% sold — cheapest avails, good for reach
buyers"* and the plan's advice *"weighting Evening"* are therefore **not comparable to what the user
would actually book.** Genie would confidently tell someone to buy a daypart that means something
else in the booking flow.

### 6.3 Loop length conflict

- Avg Dwell annotation: *"on a **120s** loop, a viewer can catch ~1–2 spots"*
- Launch wizard: *"A **60-second** loop holds 6 static (10s) or 4 videos (15s)"*

Loop length is a direct multiplier on impressions, frequency and therefore CPM and the entire
Calculator. One of these is wrong.

### 6.4 Campaign counts disagree on one screen

Command Centre: stat tile **"42 Active campaigns (12 paused)"** · Gross Billing card **"from 18
Campaigns"** · Gross Billing annotation **"Σ campaign value (42 campaigns) = ₹2.76 Cr"**.

Three numbers, one screen. If the annotation's formula is right, the card's "18" is wrong.

### 6.5 Wallet balance disagrees across screens

Command Centre **₹15,75,000** · Wallet Management "Main Wallet Balance" **₹12,30,000** · AdGuide plan
*"Reserves ₹2.50 L from ₹12.30 L"*.

AdGuide agrees with the Wallet screen, not the Command Centre. Likely the Command Centre figure
includes sub-wallets (₹12.30L main + ₹29.30L sub = ₹41.60L "Total Credits owned" — but that doesn't
reconcile to ₹15.75L either). **The wallet hierarchy needs one definition: what does "Wallet Balance"
on the Command Centre mean?**

### 6.6 Nav label inconsistency

"**Inventory** Management" (Command Centre, Launch wizard) vs "**Screen** Management" (Wallet frame
nav). Genie's NAVIGATE intent resolves on these labels, so they must be one name.

### 6.7 Campaign Manager's AdGuide is not role-aware — SEVERE

The CM variant's opener is **byte-identical** to the admin's:

> "**7 campaigns** are waiting for **your** approval, and **₹1.90 Cr** of partner billing is due 01 Jun."

But a `CAMPAIGN_MANAGER` cannot approve — the OTP goes to *"the admin's registered mobile"* — and
`PermissionBundles` grants that role `VIEW_WALLET` + `VIEW_INVOICES`, with no approval or billing
permission at all.

**Genie's opener must be computed per role from permissions, not templated.** This makes role-aware
proactive messaging a v1 requirement rather than a refinement, and it needs its own eval.

### 6.8 Campaign Manager sees actions it cannot perform

The CM Command Centre shows "Needs your attention" with **Top up →** and **Settle →**.
`PermissionBundles.CAMPAIGN_MANAGER` has neither `TOPUP_WALLET` nor `MARK_INVOICE_PAID`.

Either the bundle is wrong or the screen is. Genie must not offer either action to that role, so
this needs a ruling regardless of what the FE does.

> **These eight are a good outcome, not a setback.** Every one would have become a wrong answer from
> Genie in front of a customer. Finding them at ontology-authoring time is exactly what the ontology
> is for — and 6.1/6.2 are the concrete proof that metric definitions belong in a reviewed,
> versioned, code-verified store rather than in a vector index.

---

## 7. The backend gap — the biggest open risk

The screens in this deck and the 434 BFF endpoints I inventoried **do not obviously describe the
same product.** The BFF surface is ad-ops, hardware, retail and station-management oriented
(`HmStation`, `RmDevice`, `SeLead`, `KwDeviceLabel`, `AdopsBatch`). This deck is an agency-facing
partner hub: Command Centre, Brand Management, Launch wizard, sub-wallets, approvals.

Compounding it: **`ag-campaign-service` and `ag-billing-service` are referenced in `Knowledge.md`
(:9524, :9526) but are not cloned** in `Java_Backend/production/`, and `agency-dashboard-apis.md`
notes balance is *"mocked as 0 pending billing-service wallet integration."*

**Before Phase 1 can be scoped I need to know, per screen: does an API exist, is it planned, or is
this net-new?** The answer changes everything downstream:

- If most of it exists → Genie is an integration project, roughly as planned.
- If much is net-new → Genie's DATA path has nothing to read, and the sequencing must interleave
  with the backend build. `PLAN.md`'s Phase 4 ("Genie knows *your* numbers") would be blocked.

This is the one question I cannot answer from the repo, and it is worth answering before anything
else.

---

## 8. What changes in `PLAN.md`

| Change | Where |
|---|---|
| Ontology is **transcribed** from annotation cards, not authored | §3, §17 — Phase 1 shrinks |
| Ontology's second consumer is the **(i) tooltips** — one definition store | §3 |
| `advisor/` splits into **`planner/`** (the Calculator engine) + `advisors/` (the watchers) | §9 |
| The Calculator is the **largest component**, and it is deterministic | §9, §16 |
| Action model becomes **prefill → human edit → OTP approval**; Genie never executes | §8 |
| Most of the confirmation state machine is dropped | §8 |
| Financial actions are **out of scope permanently**, not deferred to Phase 10 | §8, §16 |
| **Role-aware proactive messaging** is a v1 requirement with its own eval | §9, §14 |
| Capabilities are mostly `prefill.*` / `navigate.*` | §8 |
| Launch wizard has **3 variants** (Standard/Premium × Broadcasting/Targeted) — prefill is variant-aware | §8 |
| Two-tier wallet (agency main → CM sub-wallets) is a tenancy concept Genie must model | §11 |
| **Do not seed golden sets from the mock numbers** — they are internally inconsistent (§6.4, §6.5) | §14 |
| Add Phase 0 task: **backend reality audit per screen** (§7) | §16 |

---

## 9. What I need from you

**Rulings (§6)** — these block ontology authoring:

1. "Avg Attention": ratio or duration? What does the other one get called?
2. Which daypart definition is canonical — the analytics one or the booking one?
3. Loop length: 60s or 120s?
4. "Wallet Balance" on Command Centre: main wallet, or main + sub-wallets?
5. Is "Inventory Management" or "Screen Management" the name?
6. Should the Campaign Manager see Top up / Settle at all?

**Scoping:**

7. **§7 — which screens have real APIs today?** The single highest-value answer.
8. Is the Calculator's model specified anywhere — rate cards, footfall/screen, reach de-duplication,
   spots-per-loop? Or is designing it part of this build?
9. Is the name **AdGuide** or **Genie**? The deck says AdGuide throughout; the panel subtitle is
   "Your dashboard copilot".
10. Brand Dashboard appears only in the Campaign Manager set — is there a third role (brand user),
    or is it a CM sub-screen?

**Still to transcribe** (I have read them at overview level; full annotation transcription is Phase 1
work): Brand Management, Creative Library, Inventory Management, Team Management, Profile/Terms, and
the Targeted + Premium wizard variants.
