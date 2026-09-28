# The BFF API — what the live spec settles

Source: `https://ag-dashboard-bff.qa.adgrid.ai/v3/api-docs`, fetched 2026-09-22.
Saved as `reference/bff-openapi.json`. Inspect with
`python tools/inspect_openapi.py "<path>" <method>`.

**AG Dashboard BFF API v1.0 — 236 paths, 261 operations, 620 schemas.**
106 operations sit in Genie-relevant domains.

---

## First thing: the architecture holds

This is the same `ag-dashboard-bff` layer modelled in `SERVICES.md`, on `/api/v1/**`, and it is
plainly built for this dashboard — one endpoint is literally described as *"List brands under an
AD_PARTNER (Brand Management left rail)"*.

So the access model in `ARCHITECTURE.md` §8 stands unchanged: **Genie calls the BFF with the user's
own JWT, reads only, and never writes.** Nothing needs rewriting.

---

## C1 is answered — `/api/v1/me` is exactly `UserContext`

```
GET /api/v1/me    "Identity snapshot from JWT — no downstream calls"

  userId · clientId · clientType · name · email · mobile
  primaryRole      PlatformRole (AD_PARTNER_ADMIN / ...)
  status           UserStatus (ACTIVE / INIT / SUSPENDED / BLOCKED)
  grants[]         { clientId, clientName, role, permissions[] }
```

`grants[]` gives **accessible client ids and the permission set per client, in one call**. That is
the whole of Genie's `UserContext`, and it is the endpoint S2 needs on day one.

`status` matters too: SUSPENDED or BLOCKED is a real state Genie must handle, not just an auth
failure.

---

## The wallet advisor is already built — this changes R4

```
GET /api/v1/clients/{clientId}/wallet/kpis

  adgridRevenue · apRevenueTotal · apConsumed · apRemaining
  addedYtd · addedLifetime · consumedThisMonth · consumedYtd · consumedLifetime
  unpaid
  runwayMonths           <- computed backend-side
  lowBalance             <- boolean, computed backend-side
  lowBalanceThreshold    <- the threshold itself
  currency
```

**`wallet_runway` does not need to be built.** The backend already computes the runway and the
low-balance flag, and exposes the threshold. Genie reads three fields instead of choosing a basis
and dividing by a burn rate.

**That removes R4 as a blocker for the advisor.** The ruling was needed because *Genie* had to pick
which balance to divide. It no longer does.

⚠️ One new mismatch: the API returns **`runwayMonths`**, the Figma shows **"Runway ~6 days"**.
Different units. Worth one check before Genie quotes either.

### R4's three balances now have three real sources

| Figma card | Endpoint | Field |
|---|---|---|
| Main Wallet Balance | `GET /clients/{clientId}/wallet/balance` | `balance` (also `apRemaining` on kpis) |
| In Team Sub-Wallets | `GET /clients/{clientId}/members/wallet-overview` | `totalSubWalletBalance` |
| Total Credits owned | — | computed: main + total sub |

So the disambiguation follow-up in `catalog/wallet.yaml` now has real backing for every option. The
ruling survives only as *"which one does the Command Centre card show"* — a labelling question, not
a capability one.

---

## Sub-wallets live under members, not under wallet

```
GET  /clients/{clientId}/members/wallet-overview           subWalletCount, totalSubWalletBalance
GET  /clients/{clientId}/members/{userId}/wallet/summary   one member's sub-wallet
POST /clients/{clientId}/members/{userId}/wallet/allocate/initiate     OTP
POST /clients/{clientId}/members/{userId}/wallet/deallocate/initiate   OTP
POST /clients/{clientId}/members/me/wallet/request-topup   <- a member requests their OWN top-up
```

**That last one bears directly on R6.** A campaign manager cannot top up the agency wallet — but
they *can* request a top-up of their own sub-wallet. So the answer to *"should a CM see Top up"* is
probably neither "show it" nor "hide it", but **"show a different action"**: request-topup, pointed
at their own sub-wallet.

Also: `wallet-overview` is **paginated**, and its own description warns that
`totalSubWalletBalance` *"will NOT equal the sum of this page's balances"*. Genie must never total
the visible rows — the same trap as `stores[].uniqueVisitors` in the audience contract.

---

## R7 — the endpoint exists, the fields still do not

```
POST /api/v1/dashboard/content    <- exists on the BFF

ContentWindowResponse:
  clientId · storeId · scope · zone · creative · brandId
  windowStart · windowEnd · windowDays
  suppressed · reason
  impressions · reach · storesPlayed · devicesPlayed · totalViews
  avgDwellS · avgAttention
  dwellByCohortZone[]
```

**No `genderSplit`. No `ageDistribution`. No `hourRhythm`.**

The ruling — serve the agency's audience through the content endpoint — is right, and the endpoint
is live. But the demographics `content_views` already stores are still not exposed on it. This is
exactly the additive change `asks/R7-AD-PARTNER-ANALYTICS.md` asks for, now with a confirmed
target: **add three fields to `ContentWindowResponse`**.

`suppressed` and `reason` are present, so the k-anonymity contract carries through as expected.

---

## The Planner's inputs are all live

| Need | Endpoint |
|---|---|
| Eligible screens | `POST /api/v1/campaigns/availability-search` |
| Screens with k slots free | **`POST /api/v1/inventory/heatmap`** — *"how many screens still have k slots free"* |
| Any-day availability | `POST /api/v1/campaigns/any-day-availability` |
| Earliest start | `POST /api/v1/campaigns/start-date-availability` |
| Price | `POST /api/v1/campaigns/simulate-cost` |
| Targeted floors | **`POST /api/v1/campaigns/targeted-rate-check`** |
| Wallet coverage | `GET /clients/{clientId}/wallet/kpis` |
| Past campaigns | `GET /api/v1/campaigns` |

`inventory/heatmap` and `targeted-rate-check` are both new to me and both directly useful — the
heatmap is a better availability primitive than filtering a device list, and `targeted-rate-check`
answers *"is every selection priced, and what is the floor"* in one call.

---

## Approvals are richer than modelled

```
GET  /api/v1/approvals/queue                       the checker's inbox
GET  /api/v1/approvals/mine                        the maker's own submissions
GET  /api/v1/approvals/{referenceCode}
GET  /api/v1/approvals/{referenceCode}/preview-today
POST /api/v1/approvals/{referenceCode}/approve
POST /api/v1/approvals/{referenceCode}/reject
POST /api/v1/approvals/{referenceCode}/send-back   <- not modelled
POST /api/v1/approvals/{referenceCode}/withdraw    <- not modelled
POST /api/v1/approvals/{referenceCode}/resubmit    <- not modelled
```

**Three transitions the ontology does not have.** `send-back` is distinct from `reject`; `withdraw`
is the maker pulling their own request; `resubmit` follows a send-back.

And `approvals/mine` means a **campaign manager has a real view here** — they can see and withdraw
their own submissions even though they cannot approve. The deck shows only approve and reject.

`referenceCode` is the approval's identity, matching the deck's `REQ-2608-01`.

---

## Client lifecycle explains the second gate

```
POST /clients/{clientId}/freeze/initiate             OTP step 1 of 2
POST /clients/{clientId}/suspend/initiate            OTP step 1 of 2
POST /clients/{clientId}/reactivate/initiate         OTP step 1 of 2
POST /clients/{clientId}/permanent-block/initiate    OTP step 1 of 2
```

These drive `@RequireClientOperational` and the `CAMPAIGN_WRITE` / `CAMPAIGN_WINDDOWN` split found
in the F1 audit. A frozen client can wind a campaign down but not start one — and there is now a
visible mechanism behind that.

---

## What this changes

| | Change |
|---|---|
| **C1** | ✅ Answered — `/api/v1/me`. S2 is no longer blocked on the identity endpoint |
| **R4** | Downgraded — the advisor reads `runwayMonths` / `lowBalance` rather than computing them |
| **R7** | Sharpened — endpoint confirmed live, three fields still to add |
| **R6** | Reframed — a CM should get `request-topup` for their own sub-wallet, not the agency Top up |
| **S6** | All planner inputs confirmed live, plus two better primitives |
| **Catalog** | 47 capabilities can have real `execution` paths filled in |
| **Ontology** | The metric → endpoint map can now be populated |
| **Architecture** | Unchanged — same BFF layer, same access model |

## New questions

1. **`runwayMonths` vs the Figma's "Runway ~6 days"** — which unit is right?
2. **`send-back` / `withdraw` / `resubmit`** — how do these appear in the design? The deck shows
   only approve and reject.
3. **Is this the "new API" set**, or is another surface still coming? Everything here matches the
   dashboard the designs describe.
