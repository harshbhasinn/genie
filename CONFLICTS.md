# Conflict register

Every contradiction found while transcribing the designs, in one place. Detail and evidence for each
is in `S0-ANSWERS.md` (rounds 1–10); this is the index.

**As of 2026-09-24.** 33 ids issued, R23 never assigned. R31 and R32 are new, from the live spec.
R25 is tracked here as resolved and as defect D4 — it is the same finding seen from both ends.

| | Count |
|---|---|
| ✅ Resolved | **28** |
| ⬜ Dropped (mock-value artifact) | **1** |
| 🔴 **Open — genuinely unanswered** | **3** |
| 🟠 **Defects** | **11** (3 on hold) |
| 🟣 AdGuide design gap | **1** |

> **Policy change, 2026-09-24.** Where the designs and the running system disagree, the running
> system wins and the divergence is filed as a design defect. See `RESOLUTION-POLICY.md`. This moved
> **seven conflicts out of "open"** — R4, R8, R12, R14, R15, R16, R18 — not because anyone ruled on
> them, but because the API already had an answer and we stopped waiting for a meeting to confirm it.
>
> `blocked` now means **no source exists anywhere**, not "two screens disagree". That is why the
> blocked metric count went *up* while the open conflict count went down: the honest gaps are now
> visible instead of being buried among naming arguments.

---

## 🔴 Open — genuinely unanswered (3)

Not naming disputes. These are product choices nobody has made, or data nobody serves.

| | Conflict | Blocks | Can we defer? |
|---|---|---|---|
| **R6** | **Should a Campaign Manager see Top up / Settle?** The role has no wallet permission, so the buttons would 403 | Role-gating for the CM variant of the wallet screen | ⚠️ Only if v1 ships admin-only |
| **R19** | **Brand Type (FMCG) vs Industry (Food & Beverages)** on one card — two taxonomies, one entity | Cosmetic | ✅ Yes |
| **R7 (residual)** | Per-campaign and per-motion **delivery measurement** for an ad partner | 9 metrics, all `blocked` | ❌ No — this is the real hole, and it is an API gap, not a naming one |

**R7 is half-closed.** The audience endpoint turned out to be `clientId`-scoped and *does* carry
`genderSplit`, `ageDistribution` and `hourRhythm` — see the Resolved table. What remains unserved is
delivery: impressions and revenue split by selling motion, auction supply, and anything per-campaign.

---

## 🔴 Absorbed instead of resolved (1)

| | Conflict | How |
|---|---|---|
| **R5** | Nav label vs page heading — Inventory/Screen Management, Team/User Management | Both recorded as aliases on the screen. The conflict becomes harmless rather than answered |

---

---

## 🟠 Defects — fixes, not decisions (11)

**These cannot be deferred the way a ruling can.** A deferred ruling means Genie says nothing. A
deferred defect means the *product* ships something wrong to a user.

| | Defect | Evidence |
|---|---|---|
| **D1** | ✅ **RULED 2026-09-21 — follow the contract.** Command Centre "Daypart Occupancy" re-buckets to **10–13 / 13–16 / 16–19 / 19–22** | Guide key rule 3: *"today's MORNING (10–13)"*; `TimeSlots` enum |
| **D2** | ⚠️ **REVISED 2026-09-21 — the button is correctly disabled; the *tip text* is wrong.** Launch step 4's Tip reads *"You can activate now even with pending creatives"* while the Activate button is greyed out and the checklist shows "All approved" unmet. **Fix the copy, not the behaviour** | `evidence/D2-tip-contradicts-checklist.jpg` |
| **D3** | ✅ **RULED — the lavender Settlement Notice is vague and wrong. The wallet IS debited.** Charges between ad partner and brand are covered by their contract; that panel should go or be reworded | product owner |
| **D4** | ✅ **RULED — 1920×1080 (landscape) is correct.** The Launch wizard is right; the **Creative Library upload modal** is the defect and needs changing | product owner |
| **D5** | ⏸️ **ON HOLD** — Launch step 4 cost card applies GST to the AP→Brand leg, which carries none. It also shows no AdGrid→AP figure, the one that leaves the wallet | Guide §10.1 |
| **D6** | ✅ **RULED — the Campaign Manager's AdGuide opener is vague; ignore it.** A campaign manager can never approve a campaign. The opener is computed from permissions | product owner |
| **D7** | ✅ **RULED — "camera footfall / 257 per screen" = daily impressions ÷ screen count**, an average of how many people pass one screen per day. Note the word clashes with the backend's `footfall` (visit events) | product owner |
| **D8** | ⏸️ **ON HOLD** — `PAUSE_CAMPAIGN`, `DELETE_CREATIVE`, `MARK_INVOICE_PAID` are declared but never enforced | F1 audit |
| **D9** | ⏸️ **ON HOLD** — Add User "Inventory Access" / "Campaign Access" tick-boxes have no backend equivalent | F1 audit |
| **D10** | 🔴 **NEW 2026-09-24 — three approval transitions exist in the API and in none of the designs.** `POST /approvals/{referenceCode}/send-back`, `/withdraw` and `/resubmit`. The deck models approval as approve-or-reject; the real flow has five moves, and two of them belong to the *maker*, not the checker. Now modelled in the ontology and catalog | live BFF spec |

---

## 🔴 CORRECTION, 2026-09-24 — R7 is not half-closed

**Contract v3.1 §5 reverses the reading recorded earlier the same day.** We had taken
`AudienceDashboardRequest.clientId` — *"Your client UUID"* — to mean the audience window was
reachable at agency scope, and recorded R7's demographics half as closed.

It is not. §5 states it plainly:

> `/audience` is scoped by the **store owner**. `/content` is scoped by the **creative owner**.
> These endpoints serve a NETWORK_PARTNER only. The ad-partner and LESSPAY_MERCHANT dashboards are
> separate products — the columns exist so those can reuse this data later, but nothing here serves them.

Both endpoints take the same `clientId`; **the backend applies it to a different column per
endpoint.** A shared request field is not a shared scope. Three metrics reverted to blocked:
`network_reach`, `audience_profile`, `monthly_reach`.

The original R7 ruling — *serve agency analytics through the content endpoint* — was right all
along. The mistake was looking for a way round it.

| | New id | What |
|---|---|---|
| **R33** | `/content` **does** return `reach` to an agency, but as *"the largest single day, since daily reach cannot be summed"* — a different question from the Command Centre's network-wide card. Whether the card should show delivered best-day reach instead is a **design decision**, not a mapping |

See `asks/REACH-API-SPEC.md` for what would close this: four additive fields on `/content`, no new
service and no `person_day` access.

---

## 🟡 New from the live spec, 2026-09-24 (3)

Both found by reading response schemas, not the deck. Both resolved under the policy — the API's
meaning is adopted and the divergence recorded — so neither blocks anything.

| | Conflict | Adopted |
|---|---|---|
| **R31** | **"Targeting-capable" means two different things.** The designs say *has a camera*. The API's own targeting path counts **`deviceVariant = NON_PREMIUM`** — `targeted-rate-check` returns `noInventoryCells` as *"cells this client holds no NON_PREMIUM devices in"* | Count what the API counts, and **say which was counted**. If the two sets are not identical, the targeting-capable share on the Command Centre is wrong today and nobody has noticed |
| **R32** | **Runway unit.** `WalletKpisResponse.runwayMonths` is **months**; the deck's alert strip reads *"Runway ~6 days"* | The API's unit. Genie says *months*. This also unblocked `wallet.explain_runway` entirely — the advisor never had to pick a balance, because the backend already picks one and returns `lowBalance` and `lowBalanceThreshold` with it |

---

## 🟣 AdGuide design gap (1)

| | Gap |
|---|---|
| **R29** | The Calculator offers a **Broadcasting / Targeted** toggle, but the "Recommended plan" card is entirely screen-shaped (*Screens 1,818 · City allocation*). **Targeted books no screens at all.** Either grey the toggle for v1, or design a Targeted plan card showing floor CPM, suggested bid, matched reach/day and estimated spend |

Related, same card: the reservation line must read **base × 1.18** (GST is on leg 1), and must **not
appear at all** for Targeted, which is postpaid.

---

## ✅ Resolved (28)

| | Conflict | Answer | Source |
|---|---|---|---|
| **R1** | "Avg Attention" — ratio or duration | **Ratio, 0–1.** The Command Centre is right; the content screen's "AVG ATTN 4.8s" is `avgDwellS`, misnamed | analytics code + contract v3.1 |
| **R2** | Daypart boundaries | **10–13 / 13–16 / 16–19 / 19–22**, four 3-hour bands, 10:00–22:00 | guide key rule 3 |
| **R3** | Loop length 60s or 120s | **Both, per tier.** `PROGRAM_120` premium = 120 s; `PROGRAM_60` non-premium = 60 s | guide §4 |
| **R10** | Footfall vs impressions | **Different quantities.** `footfall` = visit events; `impressions` = playback events, "NOT person data" | analytics code |
| **R11** | City taxonomy | Canonical is **`CityCode {DEL, GGN, NOI, MUM, BLR, HYD, CHN, PUN}`** — 8 codes. Display names are design's choice; **GGN** is the code | guide §4 |
| **R17** | Campaign status | **DRAFT → SCHEDULED → LIVE → COMPLETED**, plus CANCELLED. PAUSED/NOT_PLAYING computed for UI. **"Broadcasted" is a delivery mode, not a status** | guide §9.1 |
| **R20** | Tier/motion permission gate | **Does not exist.** All permissions are per-operation; tier and mode come from (ClientType, deliveryMode) and client-level allocations | guide §2.3, §2.4 |
| **R21** | "Activate" vs "Submit for approval" | **Maker–checker.** Admin activates; campaign manager submits. Only confirm needs approval | guide key rule 16; approvals banner |
| **R22** | When the wallet debits | **AP broadcast:** debit at OTP verify, base + 18% GST. **AP targeted:** postpaid, nothing at confirm. **NP:** zero | guide §10 |
| **R24** | Can pending creatives be used | **No.** Every requested slot needs an approved ad before confirm | guide key rule 8, §12.4 |
| **R26** | Objective "Footfall" | **"Objective" is not a backend field at all** — entirely design's to define | guide (absent) |
| **R27** | Named audience segments | **No such concept.** Targeting is city × shopType × ageGroups × genders | guide §13 |
| **R28** | Daypart range on a targeted campaign | **Targeted books no slots**, so it has no dayparts | guide §13 |
| **R13** | "Active Campaigns" | ✅ **RULED 2026-09-21 — use "Live", not "Active".** A design typo, wherever it appears | product owner |
| **R30** | NCR city codes | ✅ **RULED** — Faridabad = **FBD**, Ghaziabad = **GBD**, Greater Noida = **GND**. To be added to `CityCode` | product owner |
| **R25** | Creative dimensions — landscape or portrait | ✅ **RULED — 1920×1080 landscape.** Filed as defect **D4**: the Launch wizard is right, the Creative Library upload modal is wrong | product owner |

**Resolved under the 2026-09-24 policy** — the API had the answer and we adopted it rather than wait.

| | Conflict | Answer | Source |
|---|---|---|---|
| **R4** | Which "Wallet Balance" | **The main agency wallet** — `apRemaining` on `/wallet/kpis`. Sub-wallet money is committed, not available | live spec + policy |
| **R8** | Six terms for "screen" | **Four concepts, two retired.** Contracted · Live · Targeting-capable · Screens in use. All four come off one call, `/clients/{clientId}/devices/roster`, with `status` and `deviceVariant` | live spec + policy |
| **R12** | "Reach" | **Four concepts, each labelled.** And a correction: `AudienceDashboardRequest` takes `clientId`, so window-unique `uniqueVisitors` **is** reachable at agency scope — it is not owner-only as first read | live spec |
| **R14** | Premium vs Standard scope on the Command Centre | **Blended, and labelled blended.** The screen has no tier toggle | policy |
| **R15** | Age bands | **Reporting bands are the API's keys, rendered as returned. Targeting bands are the wizard's five.** They may legitimately differ; Genie says which it is showing | policy |
| **R16** | Three "verified" terms | **A share, a count and a ratio.** Genie names the unit every time | policy |
| **R18** | Four names for impressions/day | **One name: "Impressions"**, matching the API | policy |
| **R7** | AD_PARTNER audience scoping | **Half-closed.** `POST /dashboard/audience` is `clientId`-scoped and returns `genderSplit`, `ageDistribution`, `hourRhythm` — the three fields previously thought missing. Delivery by motion and by campaign is still unserved | live spec |

---

## ⬜ Dropped (1)

| | Why |
|---|---|
| **R9** | Avg Broadcasting CPM ₹95 vs ₹65 — identical labels and subtitles, only the mock values differed |

---

## How deferral works today

The ontology already carries this. Every metric has a `status`:

| status | Genie's behaviour |
|---|---|
| `verified` | Serves it |
| `pending_verification` | **Refuses** — "I'm not confident in that definition yet" |
| `blocked` | **Refuses**, and names the conflict |
| `pending_transcription` | Not served, not counted as coverage |

`tools/validate_ontology.py` reports the blocked count and ranks conflicts by how many metrics each
one holds up. **Carrying an unresolved conflict is a supported state, not a workaround** — the cost
is a silent metric, never a wrong answer.

## Three ways to handle a conflict, not two

| | Strategy | When | Example |
|---|---|---|---|
| **Resolve** | Get the ruling | The conflict changes what a number *means* | R4 — wrong wallet, wrong alerts |
| **Block** | Genie stays silent | Deferrable, and a gap is acceptable | R16, R18, R19 |
| **Absorb** | Make Genie accept both | The conflict is naming only, and both can be true | **R5** — record both as aliases; NAVIGATE works either way, and the ruling becomes optional |

**Absorbing is under-used.** Every conflict moved from Block to Absorb is one fewer thing waiting on
a meeting. R5 is the clearest candidate; parts of R8 and R18 may follow.
