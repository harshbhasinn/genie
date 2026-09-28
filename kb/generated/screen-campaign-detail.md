---
doc_id: screen-campaign-detail
title: Campaign Detail
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER, BRAND_MANAGER]
client_scope: null
status: published
owner: genie-build
verified_at: 2026-09-24
verified_against: ontology/screens/campaign-detail.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/campaign-detail.yaml` and re-run `python tools/render_kb.py`.


# Campaign Detail

How one campaign is delivering: pace against plan, spend against budget, where it is playing, when its audience is watching, and who that audience is. The richest screen in the product and the one most questions will be asked about.

**Where:** `/brands/{brandId}/campaigns/{campaignId}`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER, BRAND_MANAGER


## What this screen shows

### Time elapsed

Days elapsed as a share of the flight.

**How it is worked out:** `days elapsed / total flight days`

*unit: percent · live*

- Pure arithmetic on the campaign's own dates, so it works even while delivery is unserved. Pair it with percent-delivered only when that becomes available - elapsed time alone says nothing about pacing.

- PACING IS DEFINED BY THIS SCREEN. The panel states it plainly: "The marker shows time elapsed. Delivery tracking the marker = healthy pacing; ahead = burning fast; behind = under-delivering." Lift directly into the budget_pacing and underdelivery advisors.

- CRITICAL: a Targeted campaign chooses a pacing mode - Even or Accelerated. "Behind" is a defect under Even and the EXPECTED behaviour under Accelerated. The advisor must read campaign pacing before deciding anything is wrong.

### Active screens

*unit: count*

- R8 - 'Screens in use'. This is what the campaign booked, not what is powered on right now. For live status, the roster is the call.

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Screens in use on this campaign - CampaignMetricsResponse.totalScreens.

### Screen uptime

Share of scheduled playtime this campaign's screens were up.

*unit: percent*

- Per device only, so a campaign figure means fanning out across the campaign's screens and averaging. Genie says how many screens the average covers, or answers for one screen.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.


## What you can do here

- **Pause campaign** — needs an OTP sent to your registered mobile
- **Cancel campaign** — needs an OTP sent to your registered mobile
- **Creatives**
- **Map View**


## Not yet settled

These appear on the screen but their definition is disputed between screens, so they are deliberately not described here.

- **Impressions** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **Reach** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **Frequency** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **CPM** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **Spend** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **Verified view rate** — No field returns a camera-confirmed count or share, at any scope.
- **Verified viewers / day** — No field returns a camera-confirmed count or share, at any scope.
- **Percent delivered** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **Projected final** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **Stores** — Per-campaign delivery is not served on this surface. Genie can state the campaign's budget, state and screen count, and must say plainly that delivery is not available per campaign.
- **Audience Profile** — Demographics exist at agency scope and not at campaign scope. Offering the agency figure as if it were the campaign's would be wrong by a wide margin on any narrow buy.
