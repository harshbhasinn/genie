# F1 — authorization audit

**Question:** for every capability Genie records, is the gate we wrote the gate the backend actually
enforces?

**Why it matters:** Genie's pre-flight check is advisory — the real enforcement is `@RequireAccess`
in `ag-open-service`. If the catalog says one thing and the controller says another, Genie either
refuses something the user could do, or starts a form they cannot finish. A preflight-pass with a
backend 403 is a **catalog defect**, not a transient error, and should alert.

Audited 2026-09-22 against `ag-open-service` on disk.

---

## Method

The `Permission` enum has 29 values. I counted `@RequireAccess(... permission = Permission.X)`
usages per permission, then read the controller **impl** for anything the catalog references.

**The interfaces are not where the annotations live.** `CampaignController` carries only
`@Operation` and `@PostMapping`; the gates sit on `CampaignControllerImpl`, and a comment says so
explicitly: *"clientId nests in path so `@RequireAccess` (on impl) resolves through ScopeResolver"*.
Grepping the interface finds nothing and looks like an ungated endpoint.

---

## Finding 1 — three permissions the catalog referenced are never enforced

| Permission | `@RequireAccess` usages |
|---|---|
| `MARK_INVOICE_PAID` | **0** |
| `PAUSE_CAMPAIGN` | **0** |
| `DELETE_CREATIVE` | **0** |

They are declared in the enum and never used. Anything gated on them in the catalog was wrong.

### What actually gates those operations

| Operation | Catalog said | **Reality** |
|---|---|---|
| Pause a campaign | `PAUSE_CAMPAIGN` | **`EDIT_CAMPAIGN`** + `@RequireClientOperational(CAMPAIGN_WINDDOWN)` |
| Resume a campaign | `PAUSE_CAMPAIGN` | **`EDIT_CAMPAIGN`** + `@RequireClientOperational(CAMPAIGN_WINDDOWN)` |
| Delete a creative | `DELETE_CREATIVE` | **`EDIT_CAMPAIGN`** + `@RequireClientOperational(CAMPAIGN_WRITE)` |
| Mark an invoice paid | `MARK_INVOICE_PAID` | **`ADJUST_WALLET`** + `@RequireClientOperational(GENERAL_WRITE)` |

**All four corrected in the catalog.**

The practical consequence is not small. A role holding `EDIT_CAMPAIGN` but not `PAUSE_CAMPAIGN`
**can** pause a campaign — so Genie gating on `PAUSE_CAMPAIGN` would have refused a legitimate
request. `CAMPAIGN_MANAGER` is exactly that role.

---

## Finding 2 — there is a second gate we had not modelled at all

Every mutating endpoint carries **two** annotations, not one:

```java
@RequireAccess(clientIdExpression = SUBJECT_BY_CAMPAIGN_EXPR,
               permission = Permission.EDIT_CAMPAIGN)          // WHO you are
@RequireClientOperational(operation = ClientOperation.CAMPAIGN_WINDDOWN,
                          clientIdExpression = "@clientLookup.byCampaignId(#campaignId)")
                                                               // WHAT STATE the client is in
public ApiResponse<InitiateOtpActionResponse> initiatePauseCampaign(String campaignId) {
```

`ClientOperation` has six values: `UNSPECIFIED · CAMPAIGN_WRITE · CAMPAIGN_WINDDOWN ·
GENERAL_WRITE · GENERAL_READ · ANALYTICS_READ`.

This is the enforcement of key rule 5 — *"Only an ACTIVE client can create, edit or confirm. A
frozen or suspended client can still read, cancel and pause."* The distinction between
`CAMPAIGN_WRITE` and `CAMPAIGN_WINDDOWN` is exactly that: **winding a campaign down stays available
when the client is frozen; starting a new one does not.**

**Added to the catalog schema as `authorization.client_operation`**, and applied where known.

### Why this matters to Genie beyond correctness

It is a genuinely useful thing to be able to explain. *"You can pause this campaign but not create a
new one, because the client account is frozen"* is a far better answer than a bare 403 — and Genie
now has the vocabulary to give it.

---

## Finding 3 — permission usage is very uneven

| Permission | Usages |
|---|---|
| `VIEW_CLIENT` | 14 |
| `VIEW_CAMPAIGN` | 6 |
| `UPLOAD_CREATIVE` · `MANAGE_ASSIGNEES` · `VIEW_CLIENT_RATES` · `EDIT_CLIENT_RATES` | 2 each |
| `TOPUP_WALLET` · `ADJUST_WALLET` · `DELETE_CAMPAIGN` · `CREATE_CAMPAIGN` · `LAUNCH_CAMPAIGN` · `MANAGE_BRANDS_UNDER_AGENCY` · `VIEW_WALLET` | 1 each |
| `MARK_INVOICE_PAID` · `PAUSE_CAMPAIGN` · `DELETE_CREATIVE` | **0** |

A permission used once is a permission whose meaning is defined by a single endpoint. Worth knowing
when reasoning about what a role can do — the enum name is not a reliable guide to the gate.

---

## Rules this establishes

1. **Read the impl, never the interface.** The gate is not where the route is declared.
2. **Never infer a gate from a permission's name.** `PAUSE_CAMPAIGN` exists and pauses nothing.
   Every catalog entry cites the class and method it was read from.
3. **Record both gates.** Permission answers *who*; `client_operation` answers *what state the
   client is in*. Genie needs both to explain a refusal accurately.
4. **Re-run this audit when the new API surface lands.** The dashboard is moving to new endpoints,
   and every gate recorded here is against the current ones. This document is the procedure as much
   as the result.

---

## Still unverified

Capabilities at `pending_verification` whose gate has not yet been read from source — mostly reads,
where the risk is lower but not zero:

`wallet.explain_balance` · `wallet.explain_spending` · `wallet.explain_manager_wallets` ·
`campaign.explain_performance` · `campaign.explain_pacing` · `campaign.list` ·
`inventory.*` (all six) · `brand.*` (three of four) · `creative.*` (four of five) ·
`explain.*` (all six — these are ontology lookups with no backend gate, so `gate: NONE` is probably
right, but "probably" is what this audit exists to remove).

**These are best closed against the new API surface rather than the current one**, since the gates
may move with the endpoints.
