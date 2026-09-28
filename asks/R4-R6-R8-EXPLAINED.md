# R4, R6, R8 — the three worth resolving now

These are the three of nine open conflicts that **block a subsystem** rather than leaving a cosmetic
gap. Each has evidence crops in `asks/evidence/`.

---

# R4 · Which balance is "Wallet Balance"?

## What's on the screens

**Wallet Management** shows **three different balance concepts side by side**
(`r4-wallet_kpis.jpg`):

| Card | Sub-label |
|---|---|
| **Main Wallet Balance** | *"Available to allocate"* |
| **In Team Sub-Wallets** | *"3 campaign managers"* |
| **This Month's Consumption** | *"Agency spend · March 2026"* — a spend figure, not a balance |
| **Total Credits owned** | *"Main wallet + In team sub-wallet"* |

**Command Centre → Business Health** shows **one** card called **"Wallet Balance · Rev-Share"**, with
*"Runway ~N days · burn ₹X/day"* beneath it and a "Recent Transactions ›" link
(`r4-cc_wallet.jpg`). It does not say which of the three it is.

**AdGuide's plan card** says *"Reserves ₹2.50 L from ₹12.30 L · ₹9.80 L left"* — and reserves against
whatever this resolves to (`r4-adguide_reserves.jpg`).

## Why it can't be deferred

The **wallet runway alert** — the first item in your own "Needs your attention" strip — is computed
as `balance ÷ daily burn`. The three candidates differ by roughly 3.4× in the mocks.

- Pick **Total Credits** and the alert under-fires: it counts money already committed to campaign
  managers' sub-wallets, which the agency cannot spend.
- Pick **Main Wallet** and it reflects what is genuinely spendable.

An alert that fires at the wrong time is worse than no alert, so AdGuide will not compute it at all
until this is settled.

## What the backend says

`CAMPAIGN_CREATION_GUIDE.md` §10.4 — there are exactly **two ledgers**:

```
Agency wallet      WALLET:CLIENT:{apId}
Member sub-wallet  SUB_WALLET:USER:{userId}
```

And the payer is *"the campaign's **creator**, from their sub-wallet, if their role has one
(CAMPAIGN_MANAGER, MARKETING_AGENCY_ADMIN). **Otherwise** the agency wallet."*

So "Total Credits owned" is a **UI aggregate**, not a ledger. Money is never spent from it as such.

## Recommendation

**Main Wallet Balance**, and relabel the Command Centre card to match the Wallet screen.

Two reasons: it is the only figure that answers *"what can I still commit?"*, and AdGuide's plan card
already reserves against it — *"from ₹12.30 L"* is the Main Wallet figure, not the total.

- [ ] Main Wallet Balance *(recommended)*
- [ ] Total Credits owned
- [ ] Sum of sub-wallets
- [ ] Something else: ______________

**Second question, same card:** should the runway alert use the **agency** wallet, the **viewer's own
sub-wallet**, or both? A campaign manager's campaigns are paid from *their* sub-wallet, so an agency
wallet runway may be irrelevant to them.

---

# R6 · Should a Campaign Manager see "Top up" and "Settle"?

## What's on the screens

The **admin** Command Centre shows a "Needs your attention" strip with two inline actions
(`r6-admin_alerts.jpg`):

> ⚠ **Needs your attention (2)**
> 🕐 **Wallet runway ~6 days** · Burn ₹2.6L/day · balance ₹15.75L → **Top up →**
> 📄 **Rental invoice ₹42.0L** · Due in 8 days · 15 Apr 2026 → **Settle →**

The **Campaign Manager** Command Centre shows the **identical strip**, both actions included
(`r6-cm_alerts.jpg`). The only difference between the two variants is that "Team Management" is
missing from the CM's nav.

## Why it can't be deferred

`CAMPAIGN_CREATION_GUIDE.md` §2.3 gives the Campaign Manager's permissions:

> view, create, edit, launch (confirm), pause. **Cannot cancel** — lacks `DELETE_CAMPAIGN`.

No wallet permission at all. And §10.4 confirms a campaign manager **spends from their own
sub-wallet** — they never top up the agency wallet.

So the screen offers a campaign manager two actions their role cannot perform. AdGuide's rule is
that it must never propose an action the user cannot complete — so until this is settled, AdGuide
either stays silent about a wallet that is about to stop their campaigns, or it offers a dead end.

## Recommendation

**Show the alert, replace the action.** A campaign manager genuinely needs to know the agency wallet
is short — it will stop their campaigns — but cannot act on it. Replace the button with **"Ask your
admin"**, or make it a notification to the admin.

- [ ] Alert visible, action replaced with "Ask your admin" *(recommended)*
- [ ] Remove the whole strip for Campaign Managers
- [ ] Grant `TOPUP_WALLET` / `MARK_INVOICE_PAID` to the role

**Related, already being fixed in code:** the CM's AdGuide opening message is currently identical to
the admin's and offers to walk them through campaign approvals — which only an admin can do. That
message will be computed from the user's real permissions.

---

# R8 · Six words for "screen"

## What's on the screens

| Term | Where | Sub-label | File |
|---|---|---|---|
| **LIVE SCREENS** | Command Centre → Network & Audience | *"99.4% online · across 6 cities · on 3 Contracts"* | `r8-cc_live_screens.jpg` |
| **Owned Screens** | Inventory Management → Network | *"Rental \| Non-Premium"* | `r8-inv_network.jpg` |
| **Active Screens** | Inventory Management → Network | *"68% utilisation"* | `r8-inv_network.jpg` |
| **Targeting-capable screens** | Inventory Management → Network | *"64% camera + CV enabled"* | `r8-inv_network.jpg` |
| **Total Screens** | Inventory Management → Store Category donut, centre label | — | `r8-inv_donut.jpg` |
| **Screen Used** | Brand Management → brand detail → Brand Summary | — | `r8-brand_screen_used.jpg` |

## Why it can't be deferred

*"How many screens do I have?"* is the single most likely question anyone will ask AdGuide, and right
now there are six candidate answers with no stated relationship between them. AdGuide will not guess,
so it answers nothing — on the most obvious question on the product.

Note this is **not** about the numbers disagreeing. Some of them legitimately should differ. The
problem is that nothing says **which concept each term names**, so there is no way to know whether
two screens are contradicting each other or measuring different things.

## What the backend says

The canonical technical term is **`device`** (`device_inventory`). Meaningful distinctions that
actually exist in the data model:

| Backend concept | What it means |
|---|---|
| `batch.is_premium` / `DeviceTier` | **PREMIUM** (network-partner shops, 120 s loop) vs **NON_PREMIUM** (Lesspay merchant shops, 60 s loop) |
| `client_batch_allocation` | how much of a batch this client **holds** under a contract |
| `device_slot_capacity` | how many image/video slots this client may use on a given screen |
| device status | whether the screen is powered and reporting |
| camera + CV present | whether the screen can be targeted |

## Recommendation

**Name four concepts, retire the rest:**

| Concept | Meaning | Replaces |
|---|---|---|
| **Contracted screens** | available to this agency under its contracts | Owned Screens, Total Screens |
| **Active screens** | powered on and playing | LIVE SCREENS, Active Screens |
| **Targeting-capable screens** | has a camera and on-device intelligence | (keep as-is) |
| **Screens in use** | carrying at least one of this brand's / agency's live campaigns | Screen Used |

**Premium vs Rental / Non-Premium is a commercial model, not a count** — it should be a filter or a
qualifier, never a separate headline metric.

- [ ] Agree with the four above
- [ ] A different set — please name them: ______________

---

## Summary of what we need

| | Question | One-line answer needed |
|---|---|---|
| **R4** | Which balance is "Wallet Balance"? | Main / Total / Sub-wallets — plus whether the runway alert is agency-scoped or viewer-scoped |
| **R6** | Can a Campaign Manager Top up / Settle? | Alert-only / remove / grant the permission |
| **R8** | How many screen concepts are there? | Confirm the four, or name your own |

All three unblock work that is currently stopped: R4 unblocks the wallet advisor, R6 unblocks
role-aware alerts for the Campaign Manager variant, and R8 unblocks the most common question on the
product.
