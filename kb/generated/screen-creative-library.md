---
doc_id: screen-creative-library
title: Creative Library
source_kind: generated
audience: [AD_PARTNER_ADMIN, CAMPAIGN_MANAGER]
client_scope: null
status: published
owner: genie-build
verified_at: 2026-09-24
verified_against: ontology/screens/creative-library.yaml
effective_from: null
effective_to: null
---

> **Generated from the ontology. Do not edit.**
> Change `ontology/screens/creative-library.yaml` and re-run `python tools/render_kb.py`.


# Creative Library

One brand's ad files, their review state, and the rules for making them. Only approved creatives can be attached to a campaign, so this screen gates whether a campaign can be confirmed at all.

**Where:** `/brands/{brandId}/creatives`  
**Who can see it:** AD_PARTNER_ADMIN, CAMPAIGN_MANAGER


## What this screen shows

### Total Creatives

*unit: count · live*

### Approved - In library

Creatives cleared for use on a campaign.

*unit: count · live*

- A campaign goes LIVE only when every creative on it is approved, so this count is a launch blocker and not a vanity figure.

- aiReasonCode and aiMessage on a rejected row say why the checker refused it - read them back rather than saying 'rejected'.

- "Usable" in the backend means upload_status == UPLOADED AND review_status == APPROVED. Every gate calls that one check - attach, sub-status, slot allocation, manifest, targeted publish and the fallback resolver.

### Pending Review

Creatives a person still has to decide on.

*unit: count · live*

- A campaign goes LIVE only when every creative on it is approved, so this count is a launch blocker and not a vanity figure.

- aiReasonCode and aiMessage on a rejected row say why the checker refused it - read them back rather than saying 'rejected'.

- The AI never rejects an ad. It either approves it or sends it to a person, and if it says nothing within 5 minutes a person decides. So PENDING_REVIEW is a human queue, not a failure state.

### Rejected

*unit: count · live*

- A campaign goes LIVE only when every creative on it is approved, so this count is a launch blocker and not a vanity figure.

- aiReasonCode and aiMessage on a rejected row say why the checker refused it - read them back rather than saying 'rejected'.

### Static / Video

Split of the library between still images and video.

*unit: count · live*

- Image and video price differently and occupy different slot ladders, so the split is a commercial fact and not a file-type curiosity.


## What you can do here

- **Upload Creative**
- **Delete creative**
