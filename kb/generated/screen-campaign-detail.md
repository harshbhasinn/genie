---
doc_id: screen-campaign-detail
title: Campaign Detail
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER, BRAND_MANAGER]
client_scope: null
status: published
owner: genie-build
source_digest: 346195e57620
verified_against: ontology/screens/campaign-detail.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/campaign-detail.yaml` and re-run `python tools/render_kb.py`.


# Campaign Detail

How one campaign is delivering: pace against plan, spend against budget, where it is playing, when its audience is watching, and who that audience is. The richest screen in the product and the one most questions will be asked about.

**Where:** `/campaign/{campaignId}`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER, BRAND_MANAGER


## What this screen shows

### Impressions

Ad-views this campaign has delivered.

*unit: count*

- Impressions are playback events, not people. Reach is the de-duplicated person count.

- API 2026-10-23 - served per campaign, with a per-day series (dailyImpressions). The only one of the three delivery reads that is a measurement; its freshness is not in the contract, so it stays pending until the backend states it.

- R18 - named 'Impressions' here, 'Impressions Delivered' / 'Total Daily Impressions' / 'Daily Impression' elsewhere.

### Reach

People this campaign is booked to reach - the deliverable audience of its screens and time slots over the whole flight. A plan, not a count of people who saw the ad.

*unit: count · live*

- API 2026-10-23 (API over design) - this is the PLAN, by the contract: the field is totalDeliverableReach and the response carries one flat dailyReach. Never say a campaign "reached" this many people; say it was booked to reach them. Confirm with the backend (asks/BACKEND-CHECKS.md).

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Reach (best day) - ContentWindow.reach is max single-day distinct persons, not window-unique. Label it as such.

- The screen shows Impressions, Reach and Frequency together with reach = impressions / frequency - the same identity the Planner's reach model uses. Reassuring, but R12 still applies: four different reach concepts exist and only uniqueVisitors is window-unique.

### Spend

The campaign's cost spread evenly over its days, up to today - what the schedule has used, not a measure of plays. Equals the whole cost once the campaign has ended.

*unit: currency_inr · live*

- API 2026-10-23 (API over design) - this is the SCHEDULE, by the contract: the response carries one flat dailySpend alongside totalCost. Say "cost", never "spent". Confirm with the backend (asks/BACKEND-CHECKS.md).

- totalCost is what was COMMITTED; spendForRange is the even share of it up to the window's end. Neither is consumption by plays.

- For a broadcast campaign the wallet was debited in full at confirm (base + 18% GST), so "spend" here is delivery-weighted consumption, not a cash movement. Targeted campaigns are postpaid and accrue per impression.

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

### Stores

Distinct shops this campaign is playing in.

*unit: count*

- A store is a shop; a screen is a device inside it. One store can hold several screens.

- API 2026-10-23 - per campaign from GET /api/v1/campaigns, scope.store. Reported as returned.

- The campaign list's scope gives devices, store and cities per campaign. A store holds several screens, so the two are not interchangeable.

### Screen uptime

Share of scheduled playtime this campaign's screens were up.

*unit: percent*

- Per device only, so a campaign figure means fanning out across the campaign's screens and averaging. Genie says how many screens the average covers, or answers for one screen.

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

### Audience Profile

Gender and age mix of the people this campaign reached.

*unit: percent · refreshes about every 6 hours, and can change retroactively*

- POST /api/v1/dashboard/audience DOES return genderSplit and ageDistribution - but at CLIENT scope, across everything the agency runs. That answers 'who sees our ads', not 'who saw this campaign'. Genie may offer the agency-level figure, clearly labelled as the wider question.

- API 2026-10-23 (API over design) - GENDER is served per campaign, as a split of IMPRESSIONS (plays, not people). AGE is not served per campaign. Say both plainly.

- Age bands here are the design's five (18-24, 25-34, 35-44, 45-54, 55+); the analytics API returns AgeGroup keys since 2026-10-21 (18-24, 25-34, 35-44, 45-54, 55-64 and 65+). See R15.


## What you can do here

- **Pause campaign** — needs an OTP sent to your registered mobile
- **Cancel campaign** — needs an OTP sent to your registered mobile
- **Creatives**
- **Map View**


## Not yet settled

These appear on the screen but their definition is disputed between screens, so they are deliberately not described here.

- **Frequency** — Frequency isn't available per campaign: it needs measured reach, and the campaign's reach is its plan.
- **CPM** — CPM isn't reported per campaign yet.
- **Verified view rate** — No field returns a camera-confirmed count or share, at any scope.
- **Verified viewers / day** — No field returns a camera-confirmed count or share, at any scope.
- **Percent delivered** — Delivery against plan isn't available: nothing reports a campaign's planned impressions.
- **Projected final** — A projected finish isn't available: nothing reports a campaign's planned impressions.
