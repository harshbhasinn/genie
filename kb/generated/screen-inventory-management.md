---
doc_id: screen-inventory-management
title: Screen Management
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER]
client_scope: null
status: published
owner: genie-build
verified_at: 2026-09-24
verified_against: ontology/screens/inventory-management.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/inventory-management.yaml` and re-run `python tools/render_kb.py`.


# Screen Management

The screens this agency can buy on - how many, where, what kind of shop they sit in, how full they already are, and what they cost. This is the pool the Planner allocates from.

**Where:** `/inventory`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER

> The navigation calls this **Inventory Management** while the page heading reads **Screen Management**. Both names refer to the same screen.


## What this screen shows

### Owned Screens

*unit: count*

- R8 - 'Contracted'. This is what the agency may buy on, which is not the same as what is powered on right now.

- overviewStats.totalScreenCount on /clients/{clientId}/dashboard is the same number in one hop when no filtering is needed.

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Contracted screens - available to this agency under its contracts.

- R8 - "Owned" here sits beside "Active" and "Targeting-capable" on the same row, and the donut below says "Total Screens" while the Command Centre says "Live Screens" and the brand page says "Screen Used". Six terms, no agreed concept set.

- The sub-label "Rental | Non-Premium" mixes a COMMERCIAL MODEL with an INVENTORY TIER. Proposal: keep Premium/Non-Premium as the tier and treat Rental/Rev-Share as a separate commercial dimension, never a screen count.

### Active Screens

*unit: count*

- R8 and R13 - render this as 'Live'. Same call as contracted screens, with the status filter on.

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Live screens - powered on and playing. 'Active' is a design typo for Live.

- RULED 2026-09-21: prefer 'Live' over 'Active' wherever the design says Active.

### Targeting-capable screens

Screens with a camera and on-device intelligence, so they can be audience-matched.

*unit: count*

- R31 - see targeting_capable_share on the Command Centre. The API's targeting path counts NON_PREMIUM devices; the design says 'has a camera'. Say which was counted.

- This is the only one of the six screen terms with an unambiguous meaning, and it is the pool a TARGETED campaign can serve into.

### Live Campaigns

*unit: count · live*

- The qualifier "distinct, across all slots" differs from the Command Centre's plain count. RULED 2026-09-21 - use "Live". If a slot-instance count is genuinely needed it should be named separately, e.g. "Live placements".

### Avg Targeting CPM

Average clearing price per 1,000 targeted impressions.

*unit: cpm_inr · live*

- The floor, not the clearing price - the same caveat as the Command Centre's avg_targeted_cpm, which is this number under a different name.

- The CPM build-up is Base x Audience x Context x Format x Day-part = Floor CPM, and the auction clears above the floor only when demand competes (second-price).

### Avg Broadcasting CPM

Blended price per 1,000 broadcast impressions across static and video.

*unit: cpm_inr · live*

- The rate card gives the PRICE, not the achieved average. Weighting it by the sold image/video mix is Genie's own arithmetic and must be labelled as such.

- This is the AP-to-Brand card. Never surface the AdGrid-to-AP side - that is the agency's own cost basis.

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Command Centre figures are blended across tiers and should be labelled so. Tier is fixed per campaign.

- R14 - this screen is tier-scoped by the Premium/Standard toggle while the Command Centre's card of the same name has no toggle. Whether the Command Centre figure is blended, Standard-only or network-wide is unresolved.

### Daily Impression

Ad-views served per day across this tier's screens.

**How it is worked out:** `per-screen figure = daily impressions / number of screens`

*unit: per_day*

- The per-screen figure the card shows is this divided by devicesPlayed, which the same response returns - not by the contracted screen count, since a screen that played nothing is not in the denominator.

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Impressions per day. The per-screen figure is this divided by screen count.

- RULED 2026-09-22 - the "257/screen" sub-figure is Daily Impression divided by the screen count: an average estimate of how many people pass in front of one screen per day.

- NOTE THE TERM CLASH - the card calls that "camera footfall", but the backend's `footfall` field means visit events, and `impressions` means playback events ("NOT person data"). Same word, two senses. Genie should say "average impressions per screen" rather than repeat "camera footfall".

- R18 - a fourth name for impressions per day.


## What you can do here

- **Check Availability**
- **Rate Card**
- **Save & lock rate card** — needs an OTP sent to your registered mobile
- **Map View**
- **Export Report**


## Not yet settled

These appear on the screen but their definition is disputed between screens, so they are deliberately not described here.

- **Monthly Reach** — Window-unique reach is computed only for store owners. The ad-partner surface returns a best-single-day figure that cannot be extended to a month without the S5 frequency curve.
