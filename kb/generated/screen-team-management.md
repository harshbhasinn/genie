---
doc_id: screen-team-management
title: User Management
source_kind: generated
audience: [AD_PARTNER_ADMIN]
client_scope: null
status: published
owner: genie-build
source_digest: 966700257ac8
verified_against: ontology/screens/team-management.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/team-management.yaml` and re-run `python tools/render_kb.py`.


# User Management

The people who operate this agency's campaigns - campaign managers and external marketing-agency partners - what each may do, and how much credit sits in their sub-wallet.

**Where:** `/team`  
**Who can see it:** AD_PARTNER_ADMIN

> The navigation calls this **Team Management** while the page heading reads **User Management**. Both names refer to the same screen.


## What this screen shows

### Sub-wallet balance

Credit remaining in this user's sub-wallet.

*unit: currency_inr · live*

- Ledger account SUB_WALLET:USER:{userId}. This is what their campaigns are paid from - the payer is the campaign's CREATOR, never the approver.

### Monthly revenue performance

Revenue booked by this user's campaigns, by month.

*unit: currency_inr · live*

- creditsBurned on the same response is what the manager consumed, which is the more honest counterpart to revenue booked. revenueTrendPct and trendDirection are computed for you.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- Served by /api/v1/revenue-breakdown/{apId}/managers/{managerId}.


## What you can do here

- **Add New User** — needs an OTP sent to your registered mobile
- **Edit User Details** — needs an OTP sent to your registered mobile
- **Delete / deactivate user** — needs an OTP sent to your registered mobile
- **Add Funds** — needs an OTP sent to your registered mobile
