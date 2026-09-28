---
doc_id: screen-adguide-panel
title: AdGuide
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER]
client_scope: null
status: published
owner: genie-build
verified_at: 2026-09-24
verified_against: ontology/screens/adguide-panel.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/adguide-panel.yaml` and re-run `python tools/render_kb.py`.


# AdGuide

Genie itself - a docked panel available on every screen. Two tabs: Ask AdGuide for questions, and Calculator for building a media plan. Modelled here so Genie can explain its own behaviour and its own limits.

**Where:** `/*`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER


## What this screen shows

### Screens

Screens the recommended plan would book.

*unit: count · live*

- Availability read now is not availability held - a draft reserves nothing, and screens are held for ten minutes at confirm. A plan can go stale between planning and booking.

- BROADCASTING ONLY. A Targeted campaign reserves no screens at all, so this figure is meaningless for it - see R29.

### Impressions

Ad-views the plan is projected to deliver over the flight.

*unit: count · live*

- This counts SLOTS, which is plays, which is impressions as the platform defines them - R18, playback events and not people. It assumes every booked slot plays; downtime makes the real figure lower, which is what screen_uptime is for.

### Blended CPM

Price per 1,000 impressions across the plan's mix.

*unit: cpm_inr · live*

- Derived, and worth labelling as such next to a rate-card CPM, which is a quoted price rather than an achieved one.

### Daily burn

Average spend per day over the flight.

**How it is worked out:** `total campaign cost / flight days`

*unit: per_day · live*

- Flat across the flight, because the cost model is per slot per day. Real burn varies with delivery; this is a plan figure, not a pacing figure.

### Wallet reservation

Credit this plan would consume, and what remains after.

**How it is worked out:** `adgrid cost x 1.18, against the wallet the payer draws from`

*unit: currency_inr · live*

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Reserve base x 1.18 on the AdGrid-to-AdPartner leg. Show nothing for Targeted - it is postpaid.

- MUST BE base x 1.18. The wallet pays leg 1 (AdGrid to AdPartner) PLUS 18% GST - the coverage check is balance >= adgridCost x 1.18. Quoting the base alone understates it.

- MUST NOT APPEAR AT ALL for a Targeted campaign - those are postpaid, and nothing is charged at confirm.

- Which wallet it draws from depends on the viewer: a campaign manager's own sub-wallet if their role has one, otherwise the agency wallet.

### Headroom

How far the plan exceeds the reach target, and what to spend the surplus on.

*unit: percent · live*

- Depends on plan_reach_vs_target, which is blocked on the S5 reach model. Until then Genie can state the target and say it cannot yet score a plan against it.

- The deck's example - "Target met - 107% headroom. Spend the headroom by weighting Evening or adding Premium venues" - is RULE-BASED ADVICE computed from the plan, not LLM output. The narrator may not introduce a number absent from the plan's facts.


## What you can do here

- **Generate plan**
- **Prefill wizard**
- **Refine in Calculator**


## Not yet settled

These appear on the screen but their definition is disputed between screens, so they are deliberately not described here.

- **Reach vs target** — No window-unique reach exists for non-owned screens until S5 lands.
