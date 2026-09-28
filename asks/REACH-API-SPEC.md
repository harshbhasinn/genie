# What Genie needs from `person_day` — the smallest version that works

**To:** analytics backend
**From:** Genie (AdGuide)
**Date:** 2026-09-24
**Against:** Audience Dashboard API contract v3.1

---

## The short version

**You almost certainly do not need to build a new API.** The computation Genie needs already exists
and already runs — it is computed for store owners on `/dashboard/audience` and simply not exposed
on the creative-owner side.

The ask is **four fields on `POST /api/v1/dashboard/content`**, plus one offline sample for fitting.
Neither requires `person_day` access, a new service, or a tenant-scoping change.

| | Ask | Size | Unblocks |
|---|---|---|---|
| **A** | Four fields on the existing `/content` response | additive, same documents | the reach model, per agency |
| **B** | One offline sample export, internal, no tenant scope | a batch job, run once | a general reach curve that works for screens an agency has never bought |
| **C** | *(separate, cheap)* the per-day series §6 already calls tractable | additive | pacing advisors, sparklines |

**A alone is enough to ship S5 v1.** B makes it good. C is unrelated but free while you are in there.

---

## 1 · First, a correction on our side

We had read `AudienceDashboardRequest.clientId` — *"Your client UUID"* — as meaning the audience
window was reachable at agency scope, and recorded R7 as half-closed on that basis.

**Contract v3.1 §5 says otherwise, and §5 wins.** `/audience` is scoped by the **store owner**;
`/content` is scoped by the **creative owner**; the request field is shared and the column the
backend applies it to is not. Three metrics have been reverted to blocked and the conflict register
now records the scope boundary rather than an imagined way round it.

So this document asks for nothing on `/audience`. Everything below is on `/content`, which is the
surface an ad partner is actually entitled to.

---

## 2 · What the reach model is, in one paragraph

Genie has to answer *"how many **people** will this plan reach"* before a campaign is booked. The
platform can already say how many **impressions** it will serve. The gap between those two numbers
is **frequency** — how many times the average reached person is exposed — and frequency is not a
constant. It rises with flight length, falls with geographic spread, and rises sharply when screens
sit in the same store. Two screens in one shop see nearly the same people; two screens in different
cities see almost none.

So the model is a fitted curve, not a lookup. Fit it once, evaluate it locally at plan time.

**The consequence that matters to you: at runtime Genie calls nothing.** The model is a few
kilobytes of coefficients held in the service. There is no per-plan query, no load on your pipeline,
and no hot path through `person_day`. Everything below is about *fitting*, which happens offline and
is refreshed perhaps quarterly.

---

## 3 · Ask A — four fields on `POST /api/v1/dashboard/content`

Request is unchanged. These are additive response fields on `ContentWindowResponse`.

### A1 · `uniqueVisitors` — the single most valuable field

```
uniqueVisitors : long   // distinct people exposed to this client's creatives
                        // across the WHOLE window, de-duplicated
```

`reach` today is documented as *"conservative: the largest single day, since daily reach cannot be
summed"*. That is the right call given what is exposed — but it means the one ratio that would give
us frequency is unavailable, because `reach` (one day) and `impressions` (whole window) are
different grains and their ratio means nothing.

With window-unique `uniqueVisitors`, `impressions / uniqueVisitors` is average frequency directly.

This is the same computation `/audience` already performs; it is being applied to a different
column. **Keep `reach` as it is** — changing its meaning would break the dashboard. Add this
alongside it.

### A2 · `exposureHistogram` — the distribution, not just the mean

```json
"exposureHistogram": [
  {"exposures": 1,  "people": 1481},
  {"exposures": 2,  "people":  890},
  {"exposures": 3,  "people":  614},
  ...
  {"exposures": 10, "people":   38}      // final bin is "10 or more"
]
```

Average frequency alone cannot predict a *different* plan's reach. The shape of the distribution
can — a population where most people see an ad once behaves very differently from one where a few
see it twenty times, even at the same mean.

`cohorts[]` on `/audience` is already a four-bin version of exactly this (1 / 2–3 / 4–8 / 9+). A
ten-bin histogram is the same aggregation with finer bands. **If a full histogram is awkward, the
four cohort tiers applied to the creative-owner column would still be a large improvement over
nothing** — say so and we will fit on four bins.

### A3 · `dailyReach[]` — how reach accumulates

```json
"dailyReach": [
  {"date": "2026-07-16", "uniqueVisitors": 412, "impressions": 1338},
  {"date": "2026-07-17", "uniqueVisitors": 389, "impressions": 1291}
]
```

This is the **second axis** of the curve: how unique reach grows as a flight lengthens. Without it
we can fit frequency against screen count but not against duration, and duration is the variable
users change most often.

§6 of the contract already calls this tractable — *"the `dashboard_views` documents are already
per-day; the API collapses them"*. This is the same field §6 proposes for the KPI sparklines, so it
serves two purposes for one change.

### A4 · `crossDeviceRepeaters` — the overlap signal

```
crossDeviceRepeaters : long   // people seen on 2 or more of the screens in scope
```

This is the **overlap coefficient**, and it is the hardest thing to infer indirectly. It is what
tells the model that a concentrated buy reaches fewer people than a spread one at equal spend.
`/audience` already returns it. Same computation, creative-owner column.

`crossStoreRepeaters` would be welcome on the same terms — and the existing nullable convention is
right, for the same reason it is right on `/audience`: under a single-store filter the answer is a
forced zero, which reads as a measured zero and is a false statement.

### Suppression stays exactly as specified

`suppressed: true` with a `reason`, empty maps and empty arrays. Genie treats suppression as a real
state and says *suppressed*, never *zero* — §2's rule that a quiet window also reads as zero is
precisely why. **Do not zero-fill the new fields either**: `exposureHistogram: []` and
`dailyReach: []` under suppression, not bins of zero.

One thing we need to know rather than discover: **at what k does the histogram suppress?** If the
whole payload suppresses below 10 people, then the low-exposure end of the curve — which is the
shape of the small plans agencies actually buy — is unobservable, and the Planner must decline to
score small plans rather than extrapolating into a region it cannot see. That is a fine outcome. It
is only a problem if we find out late.

---

## 4 · Ask B — one offline sample, for the general curve

Ask A fits a curve on **one agency's own history**. That works for a plan shaped like what they
already buy, and extrapolates badly to one that is not — a new city, ten times the screens, a
fortnight instead of a month.

A general curve needs samples across the space. This is a **fitting input, not a product surface**:
it is internal, it has no tenant, it runs once and is refreshed occasionally, and it can be a file
rather than an endpoint.

**A CSV or Parquet drop is genuinely easier for both of us than an API.** One row per sampled slice:

| column | notes |
|---|---|
| `sample_id` | |
| `device_count` | the size axis — the most important single column |
| `store_count` · `city_count` | the spread axes |
| `shop_type_mix` | e.g. `GROCERY:12,PHARMACY:3` |
| `device_variant` | PREMIUM / NON_PREMIUM / mixed |
| `window_days` | the duration axis |
| `dayparts` | if the grain supports it |
| `footfall` | detection events |
| `unique_visitors` | **distinct people over the window** |
| `exposure_histogram` | JSON, as A2 |
| `cross_device_repeaters` · `cross_store_repeaters` | overlap |
| `suppressed` · `reason` | |

**Coverage matters more than volume.** A few hundred rows spanning 1 → 500 screens, 1 → 90 days and
1 → 5 cities beats fifty thousand rows that are all one shape. Log-spaced buckets on `device_count`
and `window_days` are ideal. Randomly chosen real device subsets are better than synthetic ones,
because real screens cluster in ways random ones do not — and that clustering is the effect being
measured.

If sampling is awkward, **the whole grid at a coarse grain works too**: every (city × shopType ×
deviceVariant × month) cell with its distinct-person count. Less ideal, still fittable.

**No `person_day` row access is needed for any of this.** Aggregates are strictly better — no PII to
handle, k-anonymity enforced at source, and a file we can version.

---

## 5 · Ask C — unrelated, cheap, worth doing while you are in there

The per-day series from A3, exposed the same way on `/audience`, is what §6 already identified for
the KPI sparklines. Independently of reach, it is what a **pacing advisor** needs: a campaign
delivering 40% of its impressions in the first quarter of its flight is the single most actionable
thing Genie could tell an agency, and today nothing on the ad-partner surface can see it.

Nine metrics in our register are blocked on delivery measurement. This does not close them, but it
is the cheapest brick in that wall.

---

## 6 · What we do NOT need, so nobody builds it

- **A reach endpoint that takes a plan and returns a number.** The model has to be back-tested and
  re-fitted, which is our work. Give us the observations and we will own the curve.
- **`person_day` row access.** Aggregates are better in every respect.
- **A real-time anything.** Fitting is offline; evaluation is local arithmetic.
- **Changes to `reach`, `scope`, or any shipped field.** Everything here is additive. `scope` stays
  `scope`, warts and all.

---

## 7 · What each ask unlocks, concretely

| Ask | Genie can then say |
|---|---|
| **none of it** (today) | *"This plan serves about 2.1 million impressions."* And nothing about people. |
| **A** | *"About 480,000 people, seen 4.4 times each on average — based on how your own campaigns have delivered."* |
| **A + B** | *"About 480,000 people. Spreading the same budget across Gurugram as well as Delhi would reach roughly 610,000 at the same cost, because a third of your Delhi impressions are landing on people you have already reached."* |
| **A + B + C** | …and *"this campaign is 40% delivered with 25% of its flight gone — it will finish nine days early unless you widen it."* |

The second row is the product. The third row is the reason anyone would use it twice.

---

## 8 · Answering the question that prompted this

> *Will the final output be a FastAPI service?*

Yes — **one Python service, FastAPI**, sitting beside `ag-dashboard-bff` rather than inside it.

The reasons are specific rather than preferential. The allocator is a constrained optimisation
(OR-Tools or SciPy), the reach model is a curve fit (NumPy/SciPy), and retrieval is pgvector plus an
embedding client and an evaluation harness. All three have their centre of gravity in Python, and
the alternative is reimplementing a solver in Java to avoid one deployable.

**The boundary that actually matters is not the language.** It is this: Genie holds **zero write
paths**. The browser calls Genie for answers and plans, and calls the BFF for everything that
changes state, with the user's own token. Genie reads the BFF as that user and can never act as
them. Twelve state-changing endpoints are recorded in our catalog with `method: NONE` precisely so
Genie can explain each one exactly while being structurally unable to call it.

**If `ag-genie-service` already exists as a Spring Boot service with real code in it, tell us what
is in it** — that changes the answer from "build one FastAPI service" to "keep Java for routing and
auth, add a Python sidecar for the planner and retrieval", which is two deployables and a hop we
would rather not have, but is the right call if the Java service is already carrying weight.

---

## 9 · Smallest useful first step

If only one thing gets done: **`uniqueVisitors` on `/content`, window-unique.**

One field. It is the difference between Genie saying *"2.1 million impressions"* and *"480,000
people, 4.4 times each" —* which is the difference between a cost calculator and a media planner.
