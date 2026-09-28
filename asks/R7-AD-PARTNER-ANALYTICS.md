# Ad-partner analytics — is it on the roadmap, and when?

**To:** Analytics backend owner · Product
**From:** AdGuide / Genie build
**Date:** 2026-09-18
**Ask:** a date, or a decision to descope. Not a design question.

---

## What we found

Building AdGuide's metric definitions for the Agency Admin Dashboard, we traced every Command Centre
metric to its backing API. The camera-derived ones have none for this tenant type.

`CONTRACT_REVIEW_RESPONSE.md` §5, *Scope boundary — confirmed*, states it directly:

> **These endpoints serve a NETWORK_PARTNER only.**
>
> - `/audience` is scoped by the **store owner** — everyone who entered your stores, whoever's
>   creative was on screen
> - `/content` is scoped by the **creative owner** — only your own creatives, wherever they played
>
> *"The **ad-partner** and LESSPAY_MERCHANT dashboards are **separate products**. The columns exist
> so those can reuse this data later, but **nothing here serves them.**"*

An `AD_PARTNER` has `ownsStores = false`. So `/audience` filtered on `owner_id` matches zero
`dashboard_views` documents, and the response comes back `suppressed: true`,
`reason: "k-anonymity: 0 < 10 people"`.

**That is a testable prediction.** Calling `POST /api/v1/dashboard/audience` with an ad-partner
`clientId` should return a suppressed empty payload today. If it returns data, something in our
reading is wrong and we would rather know now.

---

## What it affects

Six metrics on the Command Centre — the screen AdGuide opens on — plus the panel beneath them:

| Metric | Section |
|---|---|
| Network Reach | Network & Audience |
| Avg Attention | Network & Audience |
| Avg Dwell | Network & Audience |
| Verified Impressions | Network & Audience |
| Impressions Delivered | Delivery & Pricing |
| Audience Profile (gender, age bands) | Audience |

Everything camera-derived. The commercial metrics — wallet, billing, campaigns, inventory, rate
cards — are unaffected and have working APIs.

### It is wider than the Command Centre

Continuing through the Figma, client-scoped camera data is woven through the whole ad-partner
product, not confined to one panel:

| Screen | What it shows |
|---|---|
| **Campaign detail** | a full **Audience Profile · CAMERA** panel — gender and age bands — **per campaign** |
| **Campaign detail** | "When your audience is watching" — hourly delivery, 10AM-10PM |
| **Campaign detail** | *verified viewers/day*, *verified view rate* |
| **Brand detail** | **"Total Audience Reach / day"** — **per brand** |
| **Command Centre** | the six metrics above |

Every one of those is `client_id` grain — the `content_views` grain. So this is not a single widget
that could be quietly dropped: **option C below removes the measurement story from the whole
product**, which is the part that differentiates AdGrid from a plain DOOH network.

---

## The good news: the data exists, the serving does not

`content_views` is keyed by **`client_id`**, and an `AD_PARTNER` is a client. It already stores
`age_counts`, `gender_counts`, `hour_counts[24]`, `dwell_sum_ms`/`dwell_n`, `attn_sum`/`attn_n` —
the same raw material `dashboard_views` holds, at the grain an agency actually needs.

§5 also notes that `brandId` *"is populated only on ad-partner rows, which you never receive"* — so
**ad-partner rows exist in the pipeline today** and are filtered out for network partners.

So this is not a measurement gap. It is a serving gap.

---

## What we are asking

**One of these three, with a rough date:**

| | Option | What it involves |
|---|---|---|
| **A** | **Ship ad-partner analytics as scoped work** | Serve `content_views`-grain metrics to `AD_PARTNER` clients: demographics, hour rhythm, dwell, attention, reach. Needs the k-anonymity floor applied on the content grain and a decision on what an agency may see about stores it does not own |
| **B** | **Minimum viable slice** | Expose demographics + hour rhythm only, client-scoped. Unblocks the Audience Profile panel and leaves the rest for later |
| **C** | **Descope for now** | The Command Centre ships without its camera panel for ad partners. AdGuide will say "not available for your account yet" rather than guess |

**Our preference is B**, if A has no near date. It unblocks the most visible widget for the least
work, and the fields are already stored.

---

## Two things worth flagging while you are in here

**1. Window-unique reach does not exist for campaign planning.** The platform computes four
different things called reach:

| Field | What it is |
|---|---|
| `uniqueVisitors` | exact distinct persons over the window, from `person_day` — **owner-scoped only** |
| `reach` (content) | max single-day distinct persons — conservative, non-additive |
| `dailyReach` | *footfall* per day, despite the name |
| `estimatedMonthlyReach` | `dailyReach × days` — **gross, no de-duplication at all** |

AdGuide's campaign planner needs de-duplicated reach over a flight, across screens the agency does
not own. None of the four provides that. We are building a frequency model from `person_day` to
derive it (our work, not yours) — but if there is an existing model we should not duplicate it,
please say so.

**2. A cheap win already documented.** §7 of the same contract lists fields the backend computes
that **no screen currently renders**: `footfall`, `returning`, `crossStoreRepeaters`,
`crossDeviceRepeaters`, `avgAttention`, `stores[]`.

AdGuide can answer questions about these with no new backend work at all — *"how many people visited
two or more of my stores?"* has an exact answer that appears nowhere in the UI. We plan to surface
them. Flagging in case any are considered internal.

---

## What we do meanwhile

Not blocked overall — proceeding on the commercial metrics, which have working APIs, and on the
frequency model, which reads `person_day` directly rather than through `/audience`.

The camera metrics stay marked "unavailable for this account" in AdGuide's definitions file. It
refuses rather than guesses, so there is no risk of a wrong answer in the interim — only a gap.

**What would help most: a yes/no on A, B or C, and a rough quarter.** That tells us whether to design
the Command Centre's AdGuide experience around having audience data or around not having it.
