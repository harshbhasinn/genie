# C2 — test users with obtainable JWTs

**To:** QA / platform
**Blocks:** S2, and the verification of 67 metrics that are currently asserted rather than confirmed
**Date:** 2026-09-24

---

## Why this one is worth doing first

The ontology says 94 metrics exist, where each lives, and which field serves it. **One of them has
ever been confirmed against a running system.** The other 93 are read carefully off the OpenAPI spec
and believed.

That is a reasonable state to be in — until something is built on it. C2 is what converts belief into
evidence, and `tools/sweep.py` already exists to do the conversion in one command. Twenty-three calls
clear sixty-seven metrics.

It is also the cheapest of the four access items, and unlike C3 it needs nothing built.

---

## Step 1 · Decide the user matrix — 10 minutes, and it is not "one per role"

The instinct is one user per role. That is the wrong shape, because **a single tenant cannot test
isolation**, and isolation is the failure that matters most here.

The dangerous case is not a 500. It is an endpoint returning **200 with an empty payload** for a
client the caller does not hold. Empty renders as zero. A zero that is really an authorization
failure is the worst answer Genie can give, because it is indistinguishable from a quiet week — and
nobody files a bug about a number that looks plausible.

Detecting it requires a second agency. Hence:

| | Role | Client | What only this user can prove | Priority |
|---|---|---|---|---|
| **U1** | `AD_PARTNER_ADMIN` | Agency **A** | the main sweep — 23 endpoints, 67 metrics | **required** |
| **U2** | `CAMPAIGN_MANAGER` | Agency **A** | role-gating is real: no wallet permission, cannot approve, owns a sub-wallet | **required** |
| **U3** | `AD_PARTNER_ADMIN` | Agency **B** | the negative pass — that A's data returns **403** and not an empty 200 | **required** |
| **U4** | `NETWORK_PARTNER_ADMIN` | a store owner | contract v3.1 §5 empirically: `/dashboard/audience` serves them and not U1 | high |
| **U5** | `BRAND_MANAGER` | a brand under Agency A | the Brand Dashboard screen, and that a brand cannot create campaigns | nice to have |

U1–U3 are the set. U4 settles a question we are currently answering by quoting a document. U5 can lag.

**Agency B needs no data.** It exists to be refused. An empty tenant is fine and is arguably the
better test, because an empty tenant is exactly what a leak would look like.

---

## Step 2 · Get a programmatic token path — the step most likely to stall

This is where this request usually dies, so it is worth being blunt: **a username and password is
not enough.** If the Keycloak client does not permit the direct access grant, credentials cannot be
turned into a JWT by a script, and everything downstream stays manual.

What is needed, specifically:

- [ ] QA Keycloak **base URL** and **realm name**
- [ ] the **token endpoint** (usually `/realms/{realm}/protocol/openid-connect/token`)
- [ ] the **`client_id`** to authenticate against
- [ ] whether that client is **public or confidential** — and the secret if confidential
- [ ] **is Direct Access Grant (password grant) enabled on it?** ← the question that decides everything
- [ ] access-token **TTL**, so we know how often to re-auth

If direct grant is disabled — a perfectly reasonable security posture — the fallbacks in order of
preference:

1. **Enable it on a QA-only client** scoped to these five users. Cleanest.
2. **A service account with impersonation**, if the realm is configured for it.
3. **Copy the bearer token out of browser devtools.** Genuinely fine for the first sweep, and
   genuinely useless as the end state: S3's regression suite has to re-authenticate unattended, and
   a token that expires in fifteen minutes cannot run in CI.

**Option 3 unblocks the sweep today.** If getting 1 or 2 approved will take a week, do 3 now and 1 in
parallel — the sweep's findings are the thing with lead time, not the auth mechanism.

---

## Step 3 · Prove it works — three assertions, one command

Nothing counts as C2-done until this passes. `tools/sweep.py` runs it as its own preflight and
refuses to continue otherwise:

```bash
python tools/sweep.py --token-file u1.jwt --client-id <AGENCY_A_UUID>
```

1. the token endpoint returns a JWT
2. `GET /api/v1/me` returns **200**
3. `grants[]` contains Agency A with the permissions the role is supposed to have

Step 3 is the one worth reading rather than skimming. `/me` is the whole of `UserContext` in one
call, and if its `grants[]` shape differs from what C1 recorded, every authorization decision in the
catalog is built on a wrong assumption and we want to know on day one.

---

## Step 4 · Run the sweep — 23 calls, 67 metrics

```bash
python tools/sweep.py --token-file u1.jwt \
                      --client-id <AGENCY_A_UUID> \
                      --foreign-client-id <AGENCY_B_UUID>
```

It reads the ontology, discovers the path parameters it needs (a campaign id, a device id, a member
id) from live calls, builds minimal request bodies from the OpenAPI required fields, and reports per
endpoint whether the field each metric names is **present and non-null**.

Then it runs the negative pass with Agency B's id and flags any endpoint that answers **200** instead
of **403** — distinguishing `200 EMPTY` from `200 POPULATED`, because the first is the silent one.

Stdlib only, so it runs on a locked-down box without a `pip install`.

**Four outcomes, and they mean different things:**

| | Meaning |
|---|---|
| `ok` | field present and non-null. The metric can move to `verified` |
| `x` | 403, 500, or the named field is absent. A real defect — in the mapping or in the API |
| `~ suppressed` | `suppressed: true` with a reason. **Correct behaviour**, not a failure — and not a verification either. Re-run on a busier window |
| `~ empty` | 200 with nothing in it. The one to look at hardest — quiet tenant, or silent authorization failure? |

---

## Step 5 · Run the role pass with U2

Same command, U2's token. The expectation **inverts**: where U1 got 200, several endpoints should now
return 403 — wallet KPIs, member wallet overview, rate cards.

This is how role-gating gets verified instead of assumed. The catalog currently claims
`VIEW_WALLET`, `ADJUST_WALLET` and `EDIT_CLIENT_RATES` gate specific capabilities, and the F1 audit
already found three declared permissions that are **never enforced anywhere** — `PAUSE_CAMPAIGN`,
`DELETE_CREATIVE`, `MARK_INVOICE_PAID`. If more of them are decorative, U2 finds out.

A 403 here is a **pass**, not a failure.

---

## Step 6 · Run the boundary pass with U4

One call, and it settles a question we are currently answering by quoting a paragraph:

```bash
# as U4 (NETWORK_PARTNER_ADMIN) — expect 200
# as U1 (AD_PARTNER_ADMIN)      — expect 403
POST /api/v1/dashboard/audience  {"clientId": "...", "start": "...", "end": "..."}
```

Contract v3.1 §5 says `/audience` is store-owner-scoped and serves a network partner only. We have
taken that as authoritative and reverted three metrics to blocked on the strength of it. **One call
each way turns a quotation into a fact**, and if it comes back the other way, R7 changes shape again
and we would rather learn that now than in S4.

---

## Step 7 · Feed the results back

`sweep.py` writes `sweep-results.json`. From it:

- metrics that returned a real value → `status: verified`, with `verified_against` naming the run
- absent fields → a mapping defect, and the ontology is wrong, not the API
- any 200-for-another-tenant → a **tenant isolation defect**, which outranks everything else here
- re-run `validate_ontology`, `verify_sources --write`, `render_kb`

Expected end state: **67 metrics move from asserted to confirmed**, and the count of genuinely
verified data goes from 1 to something that justifies building on it.

---

## What C2 is not

**C2 is not C4.** They get bundled and should not be, because C4 is much slower.

- **C2** — *can I authenticate as each role, and does the field exist?*
- **C4** — *is the data behind it adversarial: a wallet at six days' runway, a campaign
  underdelivering, a brand with no data at all?*

**Steps 1–7 work perfectly well on boring data.** A field either exists in the response or it does
not, and that is what the sweep measures. C4 only starts to matter at S4, when the advisors need
edge cases to fire against.

So: do not let C2 wait for a seeded adversarial tenant. Ask for both, ship on whichever arrives first.

---

## Summary — what to actually send

> Five QA users: two on Agency A (`AD_PARTNER_ADMIN`, `CAMPAIGN_MANAGER`), one on a **second**
> agency, one `NETWORK_PARTNER_ADMIN`, one `BRAND_MANAGER`. The second agency can be empty — it
> exists to be refused.
>
> Plus the Keycloak QA realm, token endpoint and `client_id`, and confirmation of **whether the
> direct access grant is enabled**. If it is not, a bearer token copied from devtools unblocks us
> today while the proper path is sorted.

One request, one reply, and sixty-seven metrics stop being guesses.
