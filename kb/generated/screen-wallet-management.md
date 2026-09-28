---
doc_id: screen-wallet-management
title: Wallet Management
source_kind: generated
audience: [AD_PARTNER_ADMIN]
client_scope: null
status: published
owner: genie-build
source_digest: 77f8a5793227
verified_against: ontology/screens/wallet-management.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/wallet-management.yaml` and re-run `python tools/render_kb.py`.


# Wallet Management

Billing, credits, sub-wallet allocations and payment history. The agency holds one wallet and each campaign manager holds a sub-wallet beneath it; campaigns are paid from whichever applies.

**Where:** `/wallet`  
**Who can see it:** AD_PARTNER_ADMIN


## What this screen shows

### Main Wallet Balance

Prepaid credit held by the agency and available to allocate or spend.

**How it is worked out:** `top-ups minus consumption minus allocations out`

*unit: currency_inr · live*

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. The Command Centre's 'Wallet Balance' card means THIS - the main agency wallet.

- The ledger is WALLET:CLIENT:{apId}, a double-entry account in billing. Sub-wallets are separate accounts, SUB_WALLET:USER:{userId} - this figure does not include them.

- R4 - the Command Centre shows a single card called "Wallet Balance" and does not say which of the three figures on this screen it is. Recommendation is this one.

### In Team Sub-Wallets

Credit already allocated out to campaign managers' sub-wallets.

*unit: currency_inr · live*

- Committed, not spendable by the agency. Counting it as available is what makes the runway alert under-fire.

### This Month's Consumption

Agency spend in the current month.

*unit: currency_inr · live*

- A spend figure, not a balance. Do not offer it when asked 'how much do I have?'

### Total Credits owned

Main wallet plus all sub-wallet balances.

**How it is worked out:** `main_wallet_balance + sub_wallet_total`

*unit: currency_inr · live*

- A UI aggregate, not a ledger. Money is never spent from "total credits" as such - it is spent from either the agency wallet or one manager's sub-wallet.

### Low Balance Alert

Funding projected to be needed over the next 30 days at the recent spending rate.

**How it is worked out:** `recent daily burn x 30, compared against available balance`

*unit: currency_inr · live*

- THE BACKEND ALREADY COMPUTES THIS. /wallet/kpis returns runwayMonths, lowBalance and lowBalanceThreshold, so the advisor reads three fields rather than picking a balance and dividing by a burn rate. R4 is no longer a blocker here.

- UNIT MISMATCH TO CHECK - the API returns runwayMonths; the Figma card says "Runway ~6 days". Confirm which before Genie quotes either.

### Allocated

Credit moved from the agency wallet into this manager's sub-wallet.

*unit: currency_inr · live*

### Spent

Credit this manager has consumed on campaigns.

*unit: currency_inr · live*

- totalRefunded on the same response explains a balance that does not equal allocated minus spent - cancelled campaigns return money.

### Balance

Credit remaining in this manager's sub-wallet.

**How it is worked out:** `allocated minus spent`

*unit: currency_inr · live*

- The approvals screen checks this before an admin authorises - "Sub-wallet sufficient - balance vs budget". That is the subwallet_short advisor, specified by the design.


## What you can do here

- **Request Funds** — needs an OTP sent to your registered mobile
- **Allocate** — needs an OTP sent to your registered mobile
- **Export Report**
