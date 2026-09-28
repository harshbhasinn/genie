---
doc_id: screen-command-centre
title: Command Centre
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER]
client_scope: null
status: published
owner: genie-build
verified_at: 2026-09-24
verified_against: ontology/screens/command-centre.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/command-centre.yaml` and re-run `python tools/render_kb.py`.


# Command Centre

The agency's home. Network scale and audience, how revenue splits between the two selling motions, delivery and pricing, business health (money in, owed, kept), and where the screens and demand are concentrated.

**Where:** `/command-centre`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER


## What this screen shows

### Avg Attention

Of the people present at a screen, the share who actually turned to look at it.

**How it is worked out:** `faces looking ÷ faces present (on-device CV)`

*unit: percent · refreshes about every 6 hours, and can change retroactively*

- R16 - a 0-1 ratio, not a count and not a percent-typed field. Render it as a percentage but never confuse it with 'verified viewers', which is a count.

### Avg Dwell

How long, on average, a viewer stays within view of a screen.

**How it is worked out:** `Σ dwell seconds ÷ viewers`

*unit: seconds · refreshes about every 6 hours, and can change retroactively*

- Seconds. dwellByCohortZone on the same response breaks the figure down by cohort and zone.

- The deck's card adds "on a 120s loop, a viewer can catch ~1–2 spots". Loop density is per-cell (BillingCostCell.loops_per_time_slot), not a constant — see S0-ANSWERS.md R3. Reword as illustrative or make it cell-aware.

### Impressions Delivered

Total ad-views served per day across both motions.

**How it is worked out:** `network reach/day × avg ad-views per visitor (deck: 15.0M × 2.8 ≈ 42.0M/day)`

*unit: per_day · refreshes about every 6 hours, and can change retroactively*

- R18 - one name, 'Impressions'. This is the window total; divide by windowDays for the per-day figure the card shows.

- Derived from network_reach, so it inherits whatever R7 settles.

### Avg Broadcasting CPM

Blended price per 1,000 broadcast impressions across static and video.

**How it is worked out:** `Premium and Standard rates weighted by the sold mix (deck: ₹110 & ₹80 ≈ ₹95 / 1,000)`

*unit: cpm_inr · live*

- The rate card gives the PRICE, not the achieved average. Weighting it by the sold image/video mix is Genie's own arithmetic and must be labelled as such.

- This is the AP-to-Brand card. Never surface the AdGrid-to-AP side - that is the agency's own cost basis.

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Command Centre figures are blended across tiers and should be labelled so. Tier is fixed per campaign.

- The ₹110/₹80 rates are the deck's illustration. Real rates come from the rate cards.

### Avg Targeted CPM

Average price per 1,000 targeted impressions that actually cleared the auction.

**How it is worked out:** `floor + competitive uplift (deck: ₹167 + ~20% = ₹201 clearing)`

*unit: cpm_inr · live*

- This is the FLOOR, not the clearing price. The auction clears above the floor only when demand competes, and nothing on this surface returns what actually cleared. Say 'floor' every time.

- averaging says which mean produced it - SIMPLE or DEVICE_WEIGHTED. Quote it; the two differ materially on a lopsided city mix.

### Blended yield / 1,000

What the network earns per 1,000 impressions across both motions combined.

**How it is worked out:** `total revenue ÷ total impressions × 1,000`

*unit: cpm_inr · live*

- Two endpoints, two windows. Genie must request the same start and end on both or the ratio is meaningless.

- R14 - blended across tiers, because the Command Centre has no Premium/Standard toggle. Label it blended.

- Sits between the broadcast and targeted CPMs, weighted by the revenue split (deck: 80/20).

### Wallet Balance · Rev-Share

Prepaid credit that funds revenue-share consumption as campaigns run.

**How it is worked out:** `top-ups − consumption`

*unit: currency_inr · live*

- POLICY 2026-09-24 - R4 resolved to the MAIN agency wallet. Sub-wallet money is committed, not available; counting it would make the runway alert fire late.

- The same response carries runwayMonths, lowBalance and lowBalanceThreshold, all computed by the backend - the advisor reads them rather than deriving its own.

### Wallet runway

How many days the current balance lasts at the recent burn rate.

**How it is worked out:** `balance ÷ burn per day`

*unit: days · live*

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Backend computes runwayMonths and lowBalance. Check the unit before quoting.

- Drives the wallet_runway advisor. Threshold is config, not a literal.

### Gross Billing / Revenue

Total billing booked this month across every running campaign.

**How it is worked out:** `Σ campaign value; splits into broadcast + targeted`

*unit: currency_inr · live*

- The deck splits this into broadcast and targeted. The response does not - see broadcasting_revenue.

### Gross Profit

What the agency keeps after AdGrid's platform cost.

**How it is worked out:** `gross billing − platform cost; margin = gross profit ÷ gross billing`

*unit: currency_inr · live*

- This is the difference between the two legs simulate-cost returns (ad_partner_to_brand minus adgrid_to_ad_partner). The per-cell AdGrid-leg breakdown is marked INTERNAL and must not be narrated — see S0-ANSWERS.md B2.

### Platform Invoice

What AdGrid bills the agency this cycle — revenue-share plus screen rental.

**How it is worked out:** `RevShare consumption + screen rental`

*unit: currency_inr · live*

- GET /api/v1/billing/settlement-summary carries the same money as total, paid and unpaid when the view needs the paid share too.

- Drives the invoice_due advisor. The deck shows a due date on the alert strip.

### Live Screens

Screens in the contracted network that are powered on and reporting.

*unit: count*

- Read totalElements off the page envelope - do not count rows, which are one page deep.

- R8 - 'Live', not 'Active'. Contracted screens are the same call without the status filter.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- 'Live', not 'Active' - and distinct from Contracted, which is what the agency may buy on.

### Monthly Spot Inventory — Utilised

Share of the month's sellable ad slots already sold.

**How it is worked out:** `slots sold / slots available`

*unit: percent*

- Utilisation is one minus free over total, and image and video are separate ladders - combining them needs the sold mix and should be labelled.

- loopTiers gives maxImage and maxVideo per loop length, so a slot on a 120s premium screen is not the same unit as one on a 60s standard screen.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- Loop capacity differs by tier - 120s premium, 60s standard - so a slot means different things on each.

### Targeting Capable | Camera + Intelligence

Share of screens with a camera and on-device intelligence, so audience targeting can reach them.

**How it is worked out:** `targeting-capable screens / total screens`

*unit: percent*

- R31 - the design defines targeting-capable as 'has a camera'. The API's own targeting path counts NON_PREMIUM devices instead: targeted-rate-check reports noInventoryCells as 'cells this client holds no NON_PREMIUM devices in'. Until someone confirms the two sets are identical, Genie says which it counted.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- This is the pool a Targeted campaign can serve into.

### Active campaigns

*unit: count · live*

- 'Live' campaigns per R13. totalCampaignCount on the same section counts every campaign in every state, DRAFT included.

- RULED 2026-09-21: the label is "Live campaigns", not "Active" - "Active" is a design typo for the backend's LIVE state (R13/R17). Paused campaigns are excluded; PAUSED is a separate computed status.

### Brands managed

Brands under this agency.

*unit: count · live*

- GET /api/v1/analytics/{apId}/brands returns the same count as totalBrands plus a card per brand - use that one when the answer needs names.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- An agency is an AD_PARTNER; its brands are BRAND_UNDER_AD_PARTNER and cannot create campaigns themselves.

### Screen uptime

Share of scheduled playtime the screens were actually up, from proof-of-play.

*unit: percent*

- Per device only. A network figure means fanning out over the roster and averaging, which Genie must not do silently - either say it is an average over N screens, or answer for one screen.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

### Share of voice

Share of each loop this agency's campaigns occupy, averaged across screen sections.

*unit: percent*

- Share of voice is this client's slots over the loop's capacity. A client's share of a screen is fixed when its contract starts - clients never compete for the same slots, so this does not move with anyone else's buying.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- A client's share of a screen is fixed when its contract starts - clients never compete for the same slots.

### Fill Rate by Section

Sold share of avails, split by position in the loop.

*unit: percent*

- grid is untyped in the spec, so whether it carries the zone axis - HERO, PROMO_STRIP, SHOWCASE - has to be confirmed against a live response before this is served.

- POST /api/v1/dashboard/content-performance returns zones[] for PERFORMANCE by zone, which is a different question from how full each zone is.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- Zones are HERO, PROMO_STRIP and SHOWCASE. The deck's 'Top' and 'Bottom' map onto these.

### Location Distribution

Screens, billings and growth by city.

*unit: count*

- The spec collapses several row shapes onto one anonymous schema named Row, so the city row's real fields cannot be confirmed statically. Read a live response before serving this.

- City codes are DEL, GGN, NOI, with FBD, GBD and GND to be added. GGN renders as Gurugram.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- City codes: DEL, GGN, NOI, plus FBD, GBD, GND to be added. GGN renders as Gurugram.

- City naming is inconsistent across the product — this widget says "Gurgaon" while the AdGuide plan card says "Gurugram". One of them must win before Genie names cities.

### Daypart Occupancy

Fill rate by time-of-day.

*unit: percent*

- Same untyped-grid caveat as fill_rate_by_section. Dayparts are MORNING 10-13, AFTERNOON 13-16, EVENING 16-19, NIGHT 19-22.

- A daypart stays bookable until it ends, so today's later dayparts are usually still sellable.

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Bands are 10-13 / 13-16 / 16-19 / 19-22 over a 10:00-22:00 day. The Command Centre chart is being corrected.

### Top Brands

Brands ranked by billings this month.

*unit: currency_inr · live*

- Ranked by revenue in the requested window. The deck's commercial-model column - Rev-Share, Rental, Mixed - is not on this response; it lives on the contract.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- Each row carries a commercial model - Rev-Share, Rental or Mixed.

- Each row carries a commercial model: Rev-Share, Rental or Mixed.


## Not yet settled

These appear on the screen but their definition is disputed between screens, so they are deliberately not described here.

- **Network Reach** — Network-wide addressable reach is a store-owner figure. An ad partner can only ever see who saw its own creatives, and only as a best-single-day number.
- **Verified Impressions** — No field returns a camera-confirmed share. Guessing at impressions over totalViews would put an invented percentage next to real ones.
- **Broadcasting** — Revenue comes back as one number. Nothing on this surface splits it by broadcasting versus targeted.
- **Targeted** — Revenue comes back as one number. Nothing on this surface splits it by broadcasting versus targeted.
- **Matched avails** — Auction supply and clearing figures are not served on the dashboard BFF at all.
- **Won & filled** — Auction supply and clearing figures are not served on the dashboard BFF at all.
- **Unsold matched opportunity** — Auction supply and clearing figures are not served on the dashboard BFF at all.
- **Yield target vs broadcast** — Auction supply and clearing figures are not served on the dashboard BFF at all.
- **Intelligence Efficiency** — No endpoint returns a measurement-quality score. Genie can explain what it means and say the number is not served.
- **Audience Profile** — genderSplit, ageDistribution and hourRhythm exist only on the store-owner endpoint. No population demographic is served to an ad partner.
