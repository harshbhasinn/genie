# AdGuide — six things to verify in the platform backend

**To:** Platform backend (campaign · billing · user services)
**From:** AdGuide / Genie build
**Date:** 2026-09-18
**Ask:** short factual answers. Nothing here needs a design decision.

Separate from the analytics ask (`R7-AD-PARTNER-ANALYTICS.md`) and the design rulings — different
team, different questions.

---

## 1 · Does the Premium/Standard + Broadcasting/Targeted access gate exist? **(likely answered — please just confirm)**

> **Update 2026-09-18.** `CAMPAIGN_CREATION_GUIDE.md` §2.3 lists every role's campaign permissions
> and they are all **per-operation**; tier and mode are constrained by **(ClientType, deliveryMode)**
> and by client-level `client_batch_allocation` holdings, never per user. That points to **UI-only**.
> We would still like a yes/no rather than inferring it.


The "Add New User" form in Team Management grants a Campaign Manager access along two dimensions:

| Control | Values |
|---|---|
| **Inventory Access** * | Premium Inventory · Standard Inventory |
| **Campaign Access** * | Broadcasting Campaigns · Targeted Campaigns — *"Which campaign types this manager is allowed to create."* |

The `Permission` enum in `ag-common-util` is **per-operation** — `CREATE_CAMPAIGN`, `EDIT_CAMPAIGN`,
`LAUNCH_CAMPAIGN`, `UPLOAD_CREATIVE` and so on. We can find **no tier dimension and no motion
dimension**: nothing resembling `ACCESS_PREMIUM_INVENTORY` or `CREATE_BROADCASTING_CAMPAIGN`.

**Which is it?**

- [ ] The gate exists elsewhere — a column on the user/access row rather than a `Permission`.
      **Where?** ______________________
- [ ] It is enforced at the service layer, not by an annotation. **Where?** ______________________
- [ ] **It is currently UI-only.**

**Why we are asking.** If it is UI-only, a Campaign Manager restricted to "Standard Inventory" in the
form could still create a Premium campaign through the API. AdGuide forwards the end user's own JWT
and holds no elevated credential, so it would inherit exactly that behaviour — it would succeed on
the user's behalf at doing something the UI told them they could not do.

**We have not tested this and are not claiming a vulnerability.** But AdGuide's pre-flight check can
only mirror a gate the backend actually enforces, so we need to know which gate is real before we
record it.

---

## 2 · `simulate-cost` — what is its p95 latency?

`POST /api/v1/campaigns/simulate-cost`.

We have confirmed from `BillingCostCell` that it prices a **hypothetical** selection —
`device_count` is an `int64`, not a list of device ids — so our allocator can vary counts across
cells without resolving individual devices. That is exactly what we needed.

The open question is cost per call. The planner may call it **tens of times per plan** while
searching allocations, then once more for the authoritative price.

- Typical latency: ____ ms · p95: ____ ms
- [ ] Fine to call repeatedly
- [ ] Better to approximate locally and call once at the end

This changes how the allocator is built, so it is worth answering before we start S6.

---

## 3 · Is the AdGrid→AdPartner cost breakdown safe to show?

`InitiateConfirmCampaignRequest.adgrid_cost_breakdown_json` is commented:

> *"INTERNAL — never surfaced on any FE-facing message; consumed only by the wallet consumption report."*

`SimulateCampaignCostResponse` carries a similar per-cell breakdown on the AdGrid leg.

- [ ] Same restriction — never show it
- [ ] Safe to show on the simulate response

We ask because it is the agency's cost basis, and AdGuide narrates what it is given. If it is
restricted we will exclude it from the plan object entirely rather than rely on prompt discipline.

*(Corrected 2026-09-18 from `CAMPAIGN_CREATION_GUIDE.md` — an earlier draft of this document had
this backwards. For reference: **GST sits on leg 1, not leg 2.** The coverage check is
`balance >= adgridCost x 1.18` and the debit is `Dr wallet (base + GST)`. Leg 2, AP->Brand, is
recorded **without** GST. So the `adgrid_cost` field is ex-GST but the wallet debit is base x 1.18.)*

---

## 4 · Device reservation TTL — **answered, no action needed**

`CAMPAIGN_CREATION_GUIDE.md` key rules 1 and 2:

> *"A draft holds nothing. Screens are held only when the user confirms."*
> *"A hold lasts **10 minutes**, and the confirm OTP lasts **2 minutes**. Resending the OTP keeps the
> same hold."*

And §9.3: `RESERVED` rows carry `expires_at = now + 10 min`; a cleanup job moves them to
`BOOKING_CANCELLED` after expiry.

Good news for us — **a draft consumes no inventory**, so an AdGuide plan can sit unconfirmed without
holding screens. Nothing to answer here unless the above is out of date.

---

## 5 · How often do slots close between planning and confirming?

`InitiateConfirmCampaignResponse.start_date_unavailable_slots` — *"Requested start-date slots that
were excluded because they had already closed at confirm."*

- [ ] Rare — a plan minutes old is safe
- [ ] Common — always re-price before prefill

If common, AdGuide's plan card needs a visible validity caveat, not just a silent re-price.

---

## 6 · Are `ag-campaign-service` and `ag-billing-service` reachable in QA?

Neither is cloned in `Java_Backend/production/`, so we have read their protos but not their
implementations.

- [ ] Both deployed and reachable in QA — base URLs: ______________________
- [ ] Partially  - [ ] Not yet

We need this for S2 (skeleton) and S4 (live data), alongside a seeded test tenant.

---

## 7 · Campaign analytics — which of impressions, reach and spend are measured?

AdGuide now answers "how is my campaign doing?" from `GET /api/v1/campaign-analytics/{campaignId}/`
`impressions`, `/reach` and `/spend`. Read from the contract alone:

| Endpoint | Field AdGuide reads | What we concluded | Why |
|---|---|---|---|
| `/impressions` | `totalImpressions` | **measured** plays | per-day series that can vary |
| `/reach` | `totalDeliverableReach` | **the plan** — audience the booked screens and slots are expected to reach | the name, and one flat `dailyReach` |
| `/spend` | `spendForRange` | **the cost schedule** — `totalCost` spread evenly by day | one flat `dailySpend` |

So AdGuide says "booked to reach about N people" and "it cost ₹X", never "reached" or "spent".
**Please confirm — or tell us which of these is actually measured.** Also: how fresh is
`totalImpressions` (real time, hourly, a daily batch)? AdGuide stamps every figure with its age.

---

## 8 · Wallet, invoices and approval rules — three contract questions

**a. `/wallet/transactions` has no usable row type.** Its `transactions[]` is typed `Row`, and 16
different responses share that one schema name (springdoc keeps the first), so the spec shows
member-wallet fields (`brandsManaged`, `totalSpent`, …) for a transaction. AdGuide reads the
ledger from `GET /wallet/statement` instead, whose `WalletStatementLine` is typed.
- [ ] Give the inner `Row` classes distinct names (or `@Schema(name=…)`) so each is documented
- [ ] Confirm `/wallet/statement` and `/wallet/transactions?tab=All` list the same entries

**b. Date formats.** `from`/`to` on `spending-analysis` and `statement`, `fromMonth`/`toMonth` on
`consumption/monthly` and `months` on `settlements` are untyped strings. AdGuide sends
`YYYY-MM-DD` and `YYYY-MM` — and only when the user names a period; otherwise it sends none and
echoes the window the response returns. **Please confirm the formats.**

**c. Is `/wallet/settlements/summary` live for agencies?** The AdOps console says it is "still gated
behind a feature flag". When it is refused, AdGuide answers "what do I owe" from the last three
months of `/wallet/settlements` (`grandTotals.unpaid`).

**d. Can an agency read `GET /api/v1/approval-configs`?** It is not client-scoped. AdGuide uses it
only for who approves each stage; if it is refused, the answer gives the deadline and the timeout
behaviour from `/clients/{clientId}/approval-automation` and leaves approvers out.

---

## Already answered from the contracts — listed so you can correct us

| | What we concluded | From |
|---|---|---|
| `simulate-cost` prices hypotheticals at **cell granularity** | `BillingCostCell.device_count` is a count, not device ids | proto |
| The wallet debits **leg 1 (AdGrid→AP) PLUS 18% GST** — `balance >= adgridCost x 1.18`. Leg 2 (AP→Brand) carries **no** GST | guide §10.1, §10.4 |
| **Loop length is per tier**: `PROGRAM_120` premium = 120 s, `PROGRAM_60` non-premium = 60 s. `loops_per_time_slot` derives from it | guide §4 |
| `InitiateConfirmCampaign` / `CompleteConfirmCampaign` are **reserve-then-confirm**, not the OTP step | no OTP field; OTP orchestrated in `CampaignOpenServiceImpl` | proto + source |
| **Nine open-service implementations are OTP-gated** — campaign, wallet, batch, contract, deployment plan, promo banner, client admin, member, client | `OtpGrpcClient` usage | source |

That last one matters to us: AdGuide treats all nine as operations it can never complete, and hands
off to the screen instead. **If that list is wrong in either direction, please say so** — a missing
entry means AdGuide might try to start something it cannot finish.
