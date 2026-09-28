# AdGuide — vocabulary decisions we need

**To:** Design lead (+ analytics on R2)
**From:** AdGuide / Genie build
**Date:** 2026-09-18 (rev 2)
**Time to answer:** ~25 minutes. Every item has a recommendation — agreeing is a tick.

---

## Why this exists

We are building AdGuide's understanding of the dashboard as a single definitions file: every metric,
what it means, how it is computed, which API it comes from. That file feeds **two** things — the
**(i) tooltips** on every metric card, and AdGuide's answers. One source, so they cannot drift.

Transcribing the Figma turned up **20 places where the product says two different things.** Eight
have since been settled by reading the backend code and contracts — nothing needed from you. What
remains is below, in priority order.

Until each is settled, AdGuide refuses to answer about that metric rather than guess. A confident
wrong answer about a number on someone's screen is the fastest way to lose trust in the feature. But
it also means **metrics on the Command Centre are currently mute**, and the Command Centre is the
screen AdGuide opens on.

> **This is not about the numbers.** We know the Figma values are placeholders. Every item is about
> what a *word* means.

---

# A · Ratify (1) — we believe this is settled; please confirm

### R2 · Morning / Afternoon / Evening / Night

Two definitions exist:

| Where | Bands | Day covered |
|---|---|---|
| Command Centre → "Daypart Occupancy" | 6a–12p · 12–5p · 5–10p · 10p–6a | **24 h** |
| Launch wizard · **Campaign detail** | 10–1 · 1–4 · 4–7 · 7–10 | **12 h** |

**The booking definition wins, and the evidence is now strong:**

- Two screens use it (Launch wizard, campaign detail) against one that does not.
- The campaign detail screen states the operating day outright — *"hourly delivery · **10AM–10PM**"* —
  and marks Night *"not sold"*, with the caption *"Night (7–10PM) sits outside this campaign's booked
  day-parts."*
- Inventory is sold, reserved and priced per daypart in the backend; the analytics pipeline has no
  daypart concept at all, only 24 hourly buckets that each screen groups.

So the Command Centre's 24-hour banding spans hours that are **never sold**, and is the outlier.

- [ ] **Ratify: booking definition, four 3-hour bands, 10AM–10PM. Re-bucket the Command Centre chart.**
- [ ] Disagree — please explain

**We still need the authoritative hour boundaries** from the campaign service, to confirm 10–1 / 1–4 /
4–7 / 7–10 exactly.

---

# B · Decisions that block metrics (7)

### R4 · Which balance is "Wallet Balance" on the Command Centre?

The Wallet screen shows three distinct things: **Main Wallet Balance** (available to allocate),
**In Team Sub-Wallets**, **Total Credits owned** (main + sub). The Command Centre shows one card
called "Wallet Balance · Rev-Share".

**Recommendation — the Main Wallet**, relabelled to match the Wallet screen. It is what campaigns
draw from, and AdGuide's own plan card already reserves against it ("Reserves ₹X from ₹Y").

- [ ] Main wallet  - [ ] Main + sub-wallets  - [ ] Total credits

*If undecided:* the low-balance alert fires on this figure. Wrong basis, wrong alerts.

---

### R5 · Should the nav label and the page heading match?

This is systematic, not a one-off:

| Nav label | Page heading |
|---|---|
| Inventory Management | **Screen Management** |
| Team Management | **User Management** |

**Recommendation — make them match, using the nav label.** If the difference is deliberate, say so
and we will record both as aliases rather than change anything.

- [ ] Match them  - [ ] Deliberate — keep both, record as aliases

*If undecided:* "take me to inventory" may not resolve.

---

### R6 · Should a Campaign Manager see **Top up** and **Settle**?

The Campaign Manager's Command Centre shows both actions. The backend permission set for that role
grants **neither**.

**Recommendation — show the alert, remove the action.** A campaign manager needs to know the wallet
is short, because it stops their campaigns. Replace the button with **"Ask your admin"**.

- [ ] Alert only  - [ ] Remove entirely for CMs  - [ ] Grant the permissions

*Already being fixed in code:* the Campaign Manager's AdGuide opening message currently promises
approvals that role cannot perform. It will be computed from real permissions.

---

### R8 · How many kinds of "screen" are there?

**Six** terms now appear across three screens:

Live Screens · Owned Screens · Active Screens · Total Screens · Targeting-capable screens ·
**Screen Used**

**Recommendation — name four concepts and retire the rest:**

| Concept | Meaning |
|---|---|
| Contracted | available to this agency under its contracts |
| Active | powered on and playing |
| Targeting-capable | has a camera and on-device intelligence |
| Owned vs Rented | a commercial model, not a count |

- [ ] Agree  - [ ] A different set — please name them

*If undecided:* "how many screens do I have" is the most likely question anyone will ask AdGuide.

---

### R11 · One city list, please

A clean two-two split:

| **Gurgaon** | **Gurugram** |
|---|---|
| Command Centre · Inventory Management | Brand Management · AdGuide plan card |

Plus structural differences: Noida and Greater Noida are separate on the Command Centre, combined on
Inventory; "Other NCR" exists on some screens only.

**Recommendation — one canonical list using Gurugram** (the current official name). Decide whether
Greater Noida is its own row, and define what falls into "Other NCR".

- [ ] Agree  - [ ] A different list — please supply it

*If undecided:* AdGuide names cities in every plan it produces. Inconsistent naming reads as broken.

---

### R13 · What is an "Active Campaign"?

| Where | Qualifier |
|---|---|
| Inventory | "**distinct, across all slots**" |
| Command Centre | a plain count, with "*n* paused" beside it |

Does "active" include paused? Is the unit a campaign or a campaign-slot instance?

**Recommendation — currently delivering, excluding paused, counted as distinct campaigns.** If
Inventory needs slot-instances, name that separately, e.g. "Active placements".

- [ ] Agree  - [ ] Other — please define

---

### R14 · Are the Command Centre's numbers Premium, Standard, or both?

Inventory Management is scoped by a **Premium / Standard** toggle. The Command Centre has none.

We note the Brand Management frame is annotated *"PREMIUM (NOT PRIORITY — WILL DISCUSS LATER)"*,
which may make this easy.

**Recommendation — blended across both tiers, labelled in the section subtitle.** Or, if Premium is
genuinely out of scope for now, say "Standard" and we will model it that way.

- [ ] Blended, labelled  - [ ] Standard only  - [ ] Add the toggle

---

# C · Naming cleanups (4) — lower severity, batch these

### R16 · Three "verified" terms

| Term | Where |
|---|---|
| **Verified Impressions** | Command Centre — a share of impressions |
| **Verified view rate** | Campaign detail KPI |
| **verified viewers/day** | Campaign detail header chip — a count of people |

At minimum a *share* and a *count* are different units. Two concepts or three?

- [ ] They are ___ distinct concepts, named: ______________________

---

### R17 · Campaign status vocabulary

| Where | Values |
|---|---|
| Brand detail → Recent Campaigns | **Active** · **Scheduled** |
| Campaign detail header | **Live** · **Broadcasted** |
| Command Centre | "42 active, 12 **paused**" |

Is "Live" the same state as "Active"? Is "Broadcasted" a status, or the delivery motion?

**Recommendation — one state machine, one vocabulary.** Suggested: Draft · Scheduled · Live ·
Paused · Completed · Cancelled, with Broadcasting/Targeted kept separately as the *motion*.

- [ ] Agree  - [ ] Other — please define

---

### R18 · Four names for impressions per day

**Impressions Delivered** (Command Centre) · **Total Daily Impressions** (brand detail) ·
**Daily Impression** (inventory) · **Impressions** (campaign detail).

- [ ] Pick one: ______________________

---

### R19 · Two industry taxonomies on one card

Brand Summary shows **Brand Type: FMCG**. Company Information, on the same screen, shows
**Industry: Food & Beverages • Soft Drinks**.

- [ ] They are different things, meaning: ______________________
- [ ] Same thing — keep: ______________________

---

# D · Already ruled — no decision needed, listed so you know

### R3 · Loop length

Two annotations disagree: *"on a 120s loop"* (Avg Dwell card) and *"a 60-second loop holds 6 static
(10s) or 4 videos (15s)"* (Launch wizard). **Neither is right.** Loop density is stored per inventory
cell in billing and varies by batch, device type and time slot.

**Both annotations should be reworded as illustrative,** or made cell-aware.

### R15 · Age bands

The Figma shows five (18–24 / 25–34 / 35–44 / 45–54 / 55+). The API returns **four** — `18-25`,
`26-35`, `36-50`, `50+` — and the contract instructs consumers to *"treat the four bucket keys as
opaque strings… render them as given; do not hard-code them."*

The backend already knows `50+` actually contains **51 and over**; the label is changing to `51+`
and no number moves. **The chart should render whatever the API returns.**

---

# E · Three things to just fix

| | What | Why |
|---|---|---|
| 1 | Content-performance: rename **"AVG ATTN (s)"** → **"Avg Dwell"** | The field behind it is documented as *"mean dwell seconds"*. "Attention" already means the 0–1 look-rate on the Command Centre, which is correct as drawn. Surface the look-rate separately as **"Engaged %"** |
| 2 | Inventory → **"Daily Impression"** card, subtitled *"Camera footfall"* | Impressions are **playback events**; footfall is **people**. The backend is explicit that impressions are "NOT person data" |
| 3 | The two loop-length annotations | See R3 |

---

## What we need back

**Section A:** ratify R2, and the daypart boundaries.
**Section B:** ticks on R4, R5, R6, R8, R11, R13, R14.
**Section C:** ticks on R16, R17, R18, R19 — these can wait a week if B cannot.

Reply in this document or give us 25 minutes, whichever is faster.

---

## One thing we found that is not a design question

The "Add New User" form grants access by **Inventory Access** (Premium/Standard) and **Campaign
Access** (Broadcasting/Targeted). Neither dimension exists in the backend's permission model, which
is per-operation. We are asking the backend team whether that gate exists elsewhere or is UI-only —
tracked separately, no action needed from design unless the answer changes the form.
