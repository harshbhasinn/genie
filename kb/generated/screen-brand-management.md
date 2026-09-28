---
doc_id: screen-brand-management
title: Brand Management
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER]
client_scope: null
status: published
owner: genie-build
verified_at: 2026-09-24
verified_against: ontology/screens/brand-management.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/brand-management.yaml` and re-run `python tools/render_kb.py`.


# Brand Management

The brands beneath this agency, and everything about one of them - its summary, where its screens are, its recent campaigns, and the people who own it. Campaigns and the creative library are both reached from here, not from the top nav.

**Where:** `/brands/{brandId}`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER


## What this screen shows

### Screen Used

*unit: count*

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Screens in use - screens carrying at least one live campaign for this brand.

- R8 - a sixth term for screens, alongside Live Screens, Owned Screens, Active Screens, Total Screens and Targeting-capable screens. Proposed canonical name is "Screens in use" = screens carrying at least one of this brand's live campaigns.

### Live Campaigns

Campaigns for this brand currently delivering.

*unit: count · live*

- RULED 2026-09-21 - the label is "Live", not "Active". "Active" is a design typo for the backend's LIVE state (R13/R17).

### Total Daily Impressions

*unit: per_day*

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. Impressions - playback events, not people. One name across the product.

- R18 - a third name for the same idea. Command Centre says "Impressions Delivered", Inventory says "Daily Impression", the campaign detail says "Impressions".

### Total Audience Reach / day

*unit: per_day · refreshes about every 6 hours, and can change retroactively*

- POLICY 2026-09-24 - resolved in favour of the API/backend over the design. See RESOLUTION-POLICY.md. BrandAnalyticsResponse.totalAudienceReach. Report as returned, with its window.

- R12 - "reach" names four different things in the platform and only uniqueVisitors is window-unique. Which this shows is unconfirmed.

### Total Booked Revenue

Value of campaigns booked for this brand.

*unit: currency_inr · live*

- Almost certainly the AdPartner-to-Brand leg (what the agency invoices the brand), which carries no GST. The AdGrid-to-AdPartner leg is internal and must never be surfaced.

### Screen Distribution by Location

Share of this brand's screens by city.

*unit: percent*

- POLICY 2026-09-24 - definition taken from the deck's annotation card, with the backend's vocabulary where they differ. See RESOLUTION-POLICY.md.

- Renders GGN as 'Gurugram' here, where the Command Centre and Inventory say 'Gurgaon' (R11).


## What you can do here

- **Add Brand** — needs an OTP sent to your registered mobile
- **Create Campaign**
- **Manage campaigns**
- **Creative Library**
