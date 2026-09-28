---
doc_id: screen-notifications-approvals
title: Campaign Approvals
source_kind: generated
audience: [AD_PARTNER_ADMIN]
client_scope: null
status: published
owner: genie-build
verified_at: 2026-09-24
verified_against: ontology/screens/notifications-approvals.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/notifications-approvals.yaml` and re-run `python tools/render_kb.py`.


# Campaign Approvals

Maker-checker. Campaigns created by campaign managers sit here until an admin authorises them with an OTP. Rejected ones go back to the manager with a remark.

**Where:** `/notifications/approvals`  
**Who can see it:** AD_PARTNER_ADMIN

> The navigation calls this **Pending Approvals** while the page heading reads **Campaign Approvals**. Both names refer to the same screen.


## What this screen shows

### Pending Approvals

Campaign requests awaiting an admin's authorisation.

*unit: count · live*

- Read the envelope, not the row count. Filter with the status parameter rather than counting client-side.

- These are APPROVAL REQUESTS, not campaigns. Each carries its own id (deck: REQ-2608-01), distinct from the campaign id. Pending / Approved / Rejected are request states, not the campaign state machine.

### Budget Awaiting Approval

Total budget of all pending requests.

*unit: currency_inr · live*

- quantityValues is a free-form object in the spec, so which key holds the budget is unknown until a real response is read. Do not guess a key name.

- The sub-label confirms manager-created campaigns are paid from the MANAGER'S SUB-WALLET, not the agency wallet.

### By Campaign Type

Split of pending requests between Broadcasting and Targeted.

*unit: count · live*

- operationCode is the operation awaiting approval, not the campaign type. Campaign confirm is one of several things that can queue here.

### Oldest Waiting

Age of the longest-waiting pending request.

*unit: days · live*

- finalDeadlineAtEpochMs matters more than age when it is present - a request close to its deadline is more urgent than an older one that is not.

- THE APPROVAL_BACKLOG ADVISOR IS SPECIFIED HERE, including why it matters - waiting risks the requested flight dates. Slots can close between request and confirm, and initiate-confirm returns start_date_unavailable_slots for exactly that case.

### Sub-wallet sufficiency

Whether the requesting manager's sub-wallet covers the campaign budget.

**How it is worked out:** `manager sub-wallet balance compared against the campaign budget`

*unit: text · live*

- This is the single most useful thing Genie can add to the approvals queue: whether the manager who raised each request can actually fund it. Both sides are real reads.

- Coverage is balance against cost plus 18% GST on the AdGrid leg, not against the bare cost.

- THE SUBWALLET_SHORT ADVISOR IS SPECIFIED HERE. Note the real coverage check at initiate-confirm is balance >= adgridCost x 1.18, so a sufficiency display that ignores GST would be optimistic by 18%.


## What you can do here

- **Approve campaign** — needs an OTP sent to your registered mobile
- **Reject**
- **Send back for changes**
- **Withdraw my request**
- **Resubmit**


## Not yet settled

These appear on the screen but their definition is disputed between screens, so they are deliberately not described here.

- **Est. impressions** — Nothing typed on the approval row returns estimated impressions or reach.
- **Est. reach** — Nothing typed on the approval row returns estimated impressions or reach.
