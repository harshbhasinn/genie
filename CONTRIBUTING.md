# Working in here, with two of us

Most of this repo is YAML and Markdown edited by hand, which means the usual git advice doesn't quite
fit. The hazards here are **duplicate ids** and **conflicts in generated files**, not merge conflicts
in code. This page is the short list of things that will otherwise cost an afternoon.

---

## 1 · Before every push, run the gate

```bash
python tools/validate_ontology.py
python tools/validate_catalog.py
python tools/validate_golden.py
python tools/verify_sources.py --check
python tools/render_kb.py --check
```

CI runs exactly these. Running them locally costs two seconds and saves a round trip.

If either `--check` fails, you changed something upstream of a generated file. **Regenerate, don't
edit the output:**

```bash
python tools/render_kb.py              # refreshes kb/generated/
python tools/verify_sources.py --write # refreshes VERIFY.md
```

Then commit the regenerated files along with the change that caused them. A change to the ontology and
its regenerated output belong in the same commit — that's what makes the staleness gate meaningful.

**Optional, and worth it — a pre-push hook:**

```bash
cat > .git/hooks/pre-push <<'EOF'
#!/bin/sh
set -e
for t in validate_ontology validate_catalog validate_golden; do python tools/$t.py >/dev/null; done
python tools/verify_sources.py --check >/dev/null
python tools/render_kb.py --check >/dev/null
EOF
chmod +x .git/hooks/pre-push
```

Hooks aren't versioned, so each of us installs it once.

---

## 2 · When a generated file conflicts — never resolve it by hand

`kb/generated/**` and `VERIFY.md` are build output. Two people regenerating them produce conflicting
diffs that are meaningless to merge line by line, and hand-resolving them produces a file that matches
neither source.

```bash
git checkout --ours kb/generated VERIFY.md   # discard both sides
python tools/render_kb.py                    # regenerate from the merged ontology
python tools/verify_sources.py --write
git add kb/generated VERIFY.md
```

The merged ontology is the truth. Regenerate from it and the conflict evaporates.

---

## 3 · Reserve id ranges, so we never mint the same one twice

Both of us will reach for "the next free number" and get the same one. Duplicate ids are worse than a
merge conflict, because git merges them cleanly and the validator has to catch them later.

| | Harsh | Partner |
|---|---|---|
| **Conflicts** `R…` | R34 – R49 | R50 – R69 |
| **Defects** `D…` | D12 – D19 | D20 – D29 |
| **Golden queries** `g…` | g107 – g199 | g200 – g299 |
| **Backend questions** `B…` | B11 – B19 | B20 – B29 |

Currently issued: **R1–R33** (R23 never assigned), **D1–D11**, **g001–g106**, **B1–B5**, **C1–C4**.

Ranges are cheap and we will never exhaust them. Renumbering later is not cheap — ids are referenced
from `blocked_by`, from gotchas, and from the golden set's `why` text.

---

## 4 · Stay in your own files where you can

Conflicts mostly vanish if we partition by file. These are one-per-concern by design:

- `ontology/screens/*.yaml` — 12 files, one per screen
- `catalog/*.yaml` — 7 files, one per domain

**The four files we will both touch, and how to handle them:**

| File | Approach |
|---|---|
| `tests/golden-queries.yaml` | **Append only, at the end of your own id range's section.** Never insert into the middle — every following line shows as changed |
| `CONFLICTS.md` | Add rows at the **bottom of the relevant table**, never re-sort. Re-sorting rewrites the whole table |
| `STATUS.md` | Regenerate the numbers from the validators rather than editing them by hand. Say who measured and when |
| `ontology/schema/*.json` · `catalog/schema/*.json` | **Flag it in the PR before you push.** A schema change can invalidate every file that validates against it |

---

## 5 · Branches and pull requests

```bash
git switch -c s1/creative-library-metrics    # or: fix/r34-daypart-labels
# ... work ...
git push -u origin s1/creative-library-metrics
```

`main` is protected: no direct pushes, CI must pass, one review. Branch names read
`<stage-or-kind>/<what>` — `s2/auth-skeleton`, `fix/r34-daypart-labels`, `docs/reach-api-spec`.

**Review each other's ontology changes properly.** A wrong metric definition is a bug that ships as a
confident wrong answer to a customer about a number on their screen, and it will not show up in any
test until someone notices the number is nonsense. It is the highest-consequence, lowest-visibility
change in this repo.

Worth asking on every ontology PR:

- Does the `field` actually exist on that endpoint? (`verify_sources.py` answers this — trust it over
  the OpenAPI summary text)
- Is the `scope_key` right? `owner_id` is everyone who entered *your stores*; `client_id` is *your
  creatives, wherever they played*. **The wrong key leaks another tenant's data.**
- Does `status: verified` carry a `verified_against` naming real evidence?
- Does `status: blocked` carry both `blocked_by` and `blocked_note`?

---

## 6 · The rules the validators enforce, so you know what you're fighting

- **`state_changing: true` requires `method: NONE`.** Genie never writes. Not negotiable, not
  configurable, and the catalog schema rejects it.
- **`verified` requires `definition`, `unit`, `source` and `verified_against`.** A status of verified
  without evidence is worse than `pending_verification`, because it stops anyone looking again.
- **`blocked` requires `blocked_by` and `blocked_note`.** And `blocked` means *nothing serves this
  anywhere* — not *two screens disagree*. Disagreement is resolved by
  [RESOLUTION-POLICY.md](RESOLUTION-POLICY.md), not by blocking.
- **Every utterance belongs to exactly one capability.** The validator catches collisions; it has
  already caught three, including two capabilities that were the same thing twice.
- **Every id in the golden set must resolve.** That is the point of it — rename a metric and the test
  set fails loudly rather than quietly testing nothing.

---

## 7 · Commit messages

One line, imperative, and say what changed rather than which files:

```
Map creative library metrics to /library/creatives
Revert audience mappings — contract v3.1 §5 scopes /audience to store owners
Add R34: daypart labels disagree between wizard and Command Centre
```

If a commit resolves or raises a numbered item, put the id in the message. `git log --grep R34` then
tells the whole story of that conflict, which is the only reason the numbering is worth maintaining.

---

## 8 · Never commit

`sweep.py` takes a real JWT and calls the QA BFF as a real user. **Its input is a credential and its
output is another tenant's numbers.** Both are gitignored — `*.jwt`, `*.token`, `sweep-results.json`,
`.env` — and neither should ever be forced past that.

If a token does land in a commit, it is leaked the moment it is pushed. Rotate it rather than trying
to rewrite history.
