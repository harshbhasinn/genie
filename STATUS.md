# Progress — S0 to S11

As of 2026-09-24. Measured from the repo, not estimated. **83 files.**

---

## Summary

| S | Stage | State | Done | Blocker |
|:--:|---|---|:--:|---|
| **0** | Unblock | 🟢 **done** | **95%** | C2 · C3 · C4 — all access, no analysis left |
| **1** | Ontology + catalog | 🟢 **done** | **100%** | none — every metric and capability is modelled or honestly blocked |
| **2** | Skeleton | 🔴 not started | 0% | **C2 · D1 provider · no Postgres/Redis** |
| **3** | Navigate + Explain | 🔴 not started | 0% | S2 · no golden set |
| **4** | Data path | 🔴 not started | 0% | S3 · C4 |
| **5** | **Reach model** | 🔴 **not started** | 0% | **C3 downgraded** — 4 additive fields on `/content`, no data grant |
| **6** | Planner | 🔴 not started | 0% | S5 |
| **7** | Calculator | 🔴 not started | 0% | S6 |
| **8** | Prefill | 🔴 not started | 0% | S6 · S7 |
| **9** | Advisors | 🔴 not started | 0% | S4 |
| **10** | RAG | 🟡 scaffolded | 20% | **D3 corpus author · Tier A empty** |
| **11** | Hardening | 🔴 not started | 0% | everything |

**S0 and S1 are closed.** Everything after them waits on access or infrastructure — none on
modelling, and none on another design review.

---

## What exists

| | Artefact | Size |
|---|---|---|
| **Ontology** | 12 screens · 94 metrics · 20 form fields · 5 approval transitions | 2,900+ lines |
| **Capability catalog** | 7 domains · **50 capabilities** · 25 follow-ups | — |
| **Knowledge base** | 12 generated docs, staleness-gated | ~7,300 words |
| **API reference** | Live BFF OpenAPI — 236 paths, 261 ops, 620 schemas | 441 KB |
| **Golden query set** | 106 queries, every capability covered, cross-checked on every run | — |
| **Tooling** | ontology · catalog · golden · KB render · **source verifier** · **live sweep runner** | 7 tools |
| **Asks** | 6 documents + 9 evidence crops | ready to send |
| **Analysis** | 10 investigation rounds, F1 audit, conflict register, resolution policy | — |

### Coverage

```
Ontology     1 verified · 67 pending_verification · 26 blocked · 0 untranscribed
             68 of 94 metrics carry an endpoint; the other 26 are blocked because
             NOTHING SERVES THEM — not because two screens disagreed
             17 of 20 form fields prefillable, 14 verified

Catalog      16 verified · 30 pending · 3 blocked   (49 capabilities)
             26 answer · 12 handoff · 9 navigate · 1 plan · 1 prefill
             8 OTP-gated — Genie can never complete these
             0 still without a path

Golden set   106 queries · all 49 capabilities covered
             53 direct · 19 follow-up · 16 refusal · 21 honest gap

Blocked      R7: 24 · R12: 3 · R29: 1 · R33: 1
```

**Blocked went up, and that is the correction working.** It now means *no source exists anywhere*,
not *two screens disagree*. Twenty-four of the twenty-six are R7 — one API gap wearing twenty-four
labels.

### The read-only invariant is enforced, not documented

Every capability declares `state_changing`, which is **not** GET-vs-POST — `simulate-cost`,
`availability-search`, `inventory/heatmap`, `dashboard/content` and `dashboard/audience` are POSTs
that change nothing. The schema rejects any capability where `state_changing: true` and Genie's
method is not `NONE`.

Twelve write endpoints are now recorded with `method: NONE`, so Genie knows what each one *is* and
can explain it precisely, while being structurally unable to call it.

---

## S0 · Unblock — 🟢 95%

**28 of 32 conflicts resolved.** Three remain genuinely open — R6, R19 and the residual half of R7 —
and none of the three is a naming argument.

The 2026-09-24 policy did most of this: where the designs and the running system disagreed, the
running system won and the divergence was filed as a defect. That moved seven conflicts out of
"open" without a single meeting.

**Two new conflicts, both found in response schemas rather than the deck:**

| | What | Adopted |
|---|---|---|
| **R31** | "Targeting-capable" = *has a camera* in the designs, `deviceVariant = NON_PREMIUM` on the API's own targeting path | Count what the API counts, and say which was counted |
| **R32** | `runwayMonths` is months; the alert strip says *"Runway ~6 days"* | Months. This also unblocked the runway advisor outright |

**One new defect:**

**D10** — `send-back`, `withdraw` and `resubmit` exist in the API and in none of the designs. The
deck models approval as approve-or-reject; the real flow has five moves, and **two of them belong to
the maker, not the checker**. All three are now modelled.

**Still open:** C2 (test users — `asks/C2-TEST-USERS.md`), C3 (now four additive fields, not a data
grant — `asks/REACH-API-SPEC.md`), C4, D3 (corpus author), and the on-hold defects D5, D8, D9.

---

## S1 · Ontology + catalog — 🟢 100%

Everything that was outstanding at the last status is closed:

| Was | Now |
|---|---|
| 19 metrics untranscribed | **0** — all 94 transcribed |
| 40 metrics without a source | **9**, and all 9 are blocked because no endpoint returns them |
| 10 capabilities without a path | **0** |
| 3 approval transitions unmodelled | **0** — modelled on the screen and as three catalog capabilities |

### What the endpoint sweep turned up

**A correction, made and reversed the same day.** We read `AudienceDashboardRequest.clientId` —
*"Your client UUID"* — as meaning the audience window was reachable at agency scope, and recorded
R7's demographics half as closed. Contract v3.1 §5 reverses it: `/audience` is scoped by the **store
owner**, `/content` by the **creative owner**, and both take the same `clientId` applied to a
different column. A shared request field is not a shared scope. Three metrics reverted to blocked.

The original R7 ruling — serve agency analytics through `/content` — was right all along.

**A static verifier now prevents the class of error that caused it.** `tools/verify_sources.py`
checks every metric's declared field against the spec's response schema on each run. Its first pass
found eleven campaign metrics pointed at an endpoint returning four fields, and `campaign_spend`
mapped to `budget` — committed value presented as spend-to-date, which overstates every unfinished
campaign.

**One roster call answers all four senses of "screen".**
`GET /clients/{clientId}/devices/roster` — unfiltered is Contracted, `status=ONLINE` is Live, and
`deviceVariant` separates the tiers. Read `totalElements`, not the row count.

**What is still missing is delivery, and it is missing consistently.** No endpoint splits revenue by
selling motion, returns auction supply, gives a camera-confirmed impression share, or reports
delivery for one campaign. Nine metrics are blocked on exactly that, all under R7. This is a single
API gap wearing nine different labels, not nine separate problems.

---

## S2 · Skeleton — 🔴 0%, three items from running

**C1 is answered.** The remaining blockers are **C2** (test users with JWTs), **D1** (provider and
region), and **a Postgres and a Redis instance**.

---

## S5 · Reach model — 🔴 0% · C3 downgraded from a grant to a build task

**No longer blocked on `person_day` access at all.** `/dashboard/content` is the person_day surface,
and the computation S5 needs already runs — it is computed for store owners on `/dashboard/audience`
and simply not exposed on the creative-owner side.

The ask is now **four additive fields on `/content`** — `uniqueVisitors` (window-unique),
`exposureHistogram`, `dailyReach[]`, `crossDeviceRepeaters` — plus one offline sample export for
fitting a general curve. See `asks/REACH-API-SPEC.md`. No PII, no collection access, no runtime load:
the fitted model is a few kilobytes of coefficients evaluated locally at plan time.

**`uniqueVisitors` alone is enough to ship S5 v1.** It is the difference between Genie saying
"2.1 million impressions" and "480,000 people, 4.4 times each".

---

## S6 · Planner — heavily de-risked

Every input is confirmed live on the BFF:

| Need | Endpoint |
|---|---|
| Eligible screens | `POST /api/v1/campaigns/availability-search` |
| Screens with k slots free | `POST /api/v1/inventory/heatmap` |
| Earliest start | `POST /api/v1/campaigns/start-date-availability` |
| Price | `POST /api/v1/campaigns/simulate-cost` |
| Targeted floors | `POST /api/v1/campaigns/targeted-rate-check` |
| Rate card | `GET /api/v1/client-rate-cards` |
| Wallet coverage | `GET /api/v1/clients/{clientId}/wallet/kpis` |

What remains genuinely unbuilt is the **allocator** and the **reach model**.

---

## S10 · RAG — 🟡 20%

12 generated docs regenerate from the ontology, with a `--check` gate that has already caught one
drift. They are now ~7,300 words, up from ~4,800, because the endpoint sweep added real provenance
to 39 metrics. The **Tier A corpus — 15–25 policy documents — is still empty**, and `kb/inbox/` has
received no files.

---

## What would move the most

| | Action | Unblocks | Cost |
|---|---|---|---|
| **1** | **Test users (C2)** — see `asks/C2-TEST-USERS.md` | S2, **and verifies 67 asserted metrics** | one reply |
| **2** | Four fields on `/content` (C3) — see `asks/REACH-API-SPEC.md` | S5, the longest pole | additive, no new service |
| **3** | Postgres + Redis, provider (D1) | **S2 → S3 → S4** | half a day |
| **3** | Ask whether agency delivery measurement is planned | the 9 blocked metrics, and whether to build around the gap | one conversation |
| **5** | Name a corpus author (D3), drop files in `kb/inbox/` | S10 | one decision |

**None of it is modelling work.** S0 and S1 are done; further analysis of the designs has nothing
left to find.

## Two questions outstanding

1. Is per-campaign and per-motion delivery measurement for ad partners on anyone's roadmap? Nine
   metrics and the whole "how is my campaign doing" answer depend on it.
2. Is the QA BFF spec the complete surface, or is another one still coming?
