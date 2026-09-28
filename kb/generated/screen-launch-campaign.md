---
doc_id: screen-launch-campaign
title: Create Campaign
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER]
client_scope: null
status: published
owner: genie-build
source_digest: 1978f64e1888
verified_against: ontology/screens/launch-campaign.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/launch-campaign.yaml` and re-run `python tools/render_kb.py`.


# Create Campaign

The four-step wizard that books a campaign. Reached from a brand, not from the top nav. This is the screen AdGuide prefills - every value on the Recommended plan card maps to a field here, and the user edits before submitting.

**Where:** `/brands/{brandId}/campaigns/new`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER


## What this screen shows

### Total Campaign Cost

What this campaign will cost, as priced by the selection made so far.

**How it is worked out:** `sum over cost cells of (device_count x rate x slots x days), with surcharges`

*unit: currency_inr · live*

- adPartnerToBrandTotalCost is what the brand pays; adgridToAdPartnerTotalCost is the agency's own cost basis and is never surfaced. totalCost is the figure the plan card means.

- Two legs are priced. The WALLET pays leg 1 (AdGrid to AdPartner) PLUS 18% GST - the coverage check is balance >= adgridCost x 1.18. Leg 2 (AdPartner to Brand) carries no GST and is recorded as a receivable.

- The wizard shows only the brand-facing leg ('Cost to Brand' / 'Base Amount'). The AdGrid-to-AdPartner per-cell breakdown is marked INTERNAL and must never be surfaced.

- Targeted campaigns are postpaid - nothing is charged at confirm.

### Estimated Impressions

Ad-views this selection is projected to deliver over the flight.

*unit: count · live*

- This counts SLOTS, which is plays, which is impressions as the platform defines them - R18, playback events and not people. It assumes every booked slot plays; downtime makes the real figure lower, which is what screen_uptime is for.

- Impressions are playback events, not people. Reach is the de-duplicated person count.

### Avg CPM

Blended price per 1,000 impressions for this selection.

*unit: cpm_inr · live*

- Derived, and worth labelling as such next to a rate-card CPM, which is a quoted price rather than an achieved one.

### Floor CPM

The minimum price per 1,000 verified impressions for this targeting.

**How it is worked out:** `Base x Audience multiplier x Context multiplier x Format multiplier x Day-part multiplier`

*unit: cpm_inr · live*

- configured is false when any selected cell is unpriced - check it before quoting a floor, or the number is an average over an incomplete grid. missingRateCells says which.

- The deck's Auction & Delivery panel gives the whole build-up. Narrower audiences raise the floor; the auction clears above it only when demand competes.

- Targeted only.


## Steps

### 1. Campaign Details

Name, flight dates, dayparts, inventory tier, creative format and objective.

| Field | Required | Notes |
|---|---|---|
| Campaign type | yes | Broadcasting, Targeted |
| Campaign Name | yes |  |
| Campaign Schedule | yes |  |
| Campaign Days |  |  |
| Time Slots | yes | Four 3-hour bands over a 10:00-22:00 sellable day. |
| Inventory Type | yes | Premium, Standard |
| Creative Type | yes | Static 10s, JPG/PNG, max 5MB. Video 15s, MP4, max 10MB. |
| Frequency (slots per minute loop) |  | Deck shows max 18 static, max 12 video. |
| Campaign Objective | yes | Brand Awareness, Product Launch, Seasonal Promotion, Tactical Campaign, Regional Campaign, Test & Learn |

### 2. Select Screens *(for broadcasting campaigns only)*

Choose how many screens to book, per city and store category. Cost recalculates live as the sliders move.

| Field | Required | Notes |
|---|---|---|
| Select Cities | yes | Delhi, Gurugram, Noida, Faridabad, Ghaziabad |
| Screens per store category | yes | Bounded by the AVAILABLE count for that city and category. |

### 2. Audience Targeting *(for targeted campaigns only)*

No screens are reserved. The ad is published to audience topics and serves into an empty slot whenever a matching person is detected. Billed per won impression.

| Field | Required | Notes |
|---|---|---|
| Gender |  | Male, Female |
| Store category context |  | High-intent categories (Pharmacy, Electronics) apply a x1.30 CPM multiplier. |
| Max CPM bid | yes | Must be at or above the floor CPM. |
| Total budget | yes | Must be > 0 at confirm. |
| Daily spend limit |  | Optional. Blank = auto-pace, capped by available matched supply. |
| Pacing | yes | Even, Accelerated |

### 3. Creative Format

Attach approved creatives to the campaign's zones and slots.

| Field | Required | Notes |
|---|---|---|
| Attached creatives | yes |  |

### 4. Campaign Summary

Review everything, accept terms, and activate or submit for approval.

| Field | Required | Notes |
|---|---|---|
| Terms & Conditions | yes | Must be ticked before the terminal action enables. |


## What you can do here

- **Save to Draft**
- **Activate** — needs an OTP sent to your registered mobile
- **Submit for approval**
- **Check Availability**


## Not yet settled

These appear on the screen but their definition is disputed between screens, so they are deliberately not described here.

- **Daily Reach** — Two separate problems on one number. R12 - the frequency curve from S5 does not exist, so impressions cannot be turned into people. R29 - the Recommended-plan card that would show it is screen-shaped, and a Targeted campaign books no screens at all, so the card is wrong for that motion even once the number arrives.
