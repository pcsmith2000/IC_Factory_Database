# Runbook

## Before the first run — the hand-placed inputs
Everything else is generated. These five are placed by a person, in one "data commit":

| file | what | where it comes from |
|---|---|---|
| `control/control-triaged.csv` | 241-row held-out control list with `triage` | the 2026-09-09 build |
| `control/seeds.csv` | ~30 IC + ~30 NOT-IC contract rows with `seed_label` | the 2026-09-09 build (`row_hash` may be blank; `--fix` derives it) |
| `control/frame_state_totals.csv` | `state,establishments` for the four core NAICS codes | Census CBP, vintage in `registry/config.yaml` |
| `prompts/CLASSIFIER-PROMPT.md` | the frozen prompt | `process/CLASSIFIER-PROMPT.md`, 2026-09-09 version |
| `control/crosswalk.csv`, `control/operator_assertions.csv` | optional; explicit links and human corrections | grow over time |

Then, in the same commit:
```
python -m pipeline.control check --fix     # validates columns, triage values, counts vs config; derives seed row_hash; rewrites control.sha256
pytest -q
```
`check` fails on any drift between the files and `registry/config.yaml` (`control.total_rows`,
`control.in_scope_rows`, `classifier.seeded_*`, `frame.establishments_total`) so the numbers in
config and the files can never disagree silently. G4 verifies the checksum on every run.

`id_registry.json` starts empty: the first run issues IC-numbers from IC-00001. Numbers from the
2026-09-09 build are not imported (its signatures were computed by different normalisation);
that build is compared to the first release with `pipeline/compare.py`, not merged into it.


## A normal quarter
1. `run.yml` fires. Watch it. Green → a release PR appears.
2. Download the workflow artifact. Review `dedupe_audit_<date>.csv`: fill the `decision`
   column (merge / keep / co-located). Review `review_queue.csv`: decide each UNCERTAIN row.
3. Commit the decisions to `control/` (they become seeds and crosswalk entries) and merge the PR.
   The count in the run record's `release.published_count` is the quotable number, with its tag.
   It counts located facilities only — `reconcile.TIER_RULES` has always said "T0 never counted",
   and since 2026-09-17 the code agrees. The record carries the whole decomposition, so quote from
   it rather than recomputing: `raw_count` (clusters) − `t0_leads` (no street address on any row)
   − `dedupe_removed` = `published_count`. T0 rows stay in the warehouse and stay queryable; they
   are leads, not facilities. `t0_leads_with_city` counts the subset locatable to a town but not a
   street, in case that boundary is ever moved.

## A gate fails
- **G1 over 2%** — look at the pairs. If they are city-string variances, the fix is
  `norm_city` in `pipeline/contract.py`, not name matching. Re-run from layer 2.
  Pairs marked *"same name + city, DIFFERENT street"* at confidence 0.40 are **not** duplicates
  and are not counted as any: one company routinely runs several plants in one city (TAS Energy
  has five in Houston). They are listed so a reviewer can catch the one case a key split — the
  same plant written "100 Main St" and "100 N Main Street". Merge one only with evidence; the
  default answer is *keep*.
- **G2 flagged clusters** — a false merge. Inspect the cluster's rows; usually two firms sharing
  a street key with a unit designator lost. Fix the key logic or add a crosswalk override.
- **G3 issued ids on a re-run** — signatures changed. Something in normalisation is
  non-deterministic. Do not publish; find it.
- **G4 checksum mismatch** — the control file changed without a logged triage edit. Restore
  or record.
- **G5 below threshold** — the prompt, the model, or the seed set changed. Check the prompt
  hash in the run record against the last passing one. A wrong seed is corrected in
  `control/seeds.csv` with a note (the resort and marina).

## Adding a source
Add an entry to `registry/sources.yaml` with class, method, `url`, `needs_classify`, traps and
`status: queued`. Write `pipeline/sources/<id>.py` from `_template.py` (fetch / parse / pull).
Check it alone: `python -m pipeline.sources.check <id>`. Flip to `active`. It is pulled next
run. Nothing else changes. Status of every fetcher: `docs/sources.md`.

## A source fails at Layer 1
The run record lists it under `layers.1_acquire.failed` with the exception. `LayoutChanged`
names the archived file under `.cache/ic-sources/<id>/<date>/` — open it, fix the parser, re-check
with `--file`. `NeedsBrowser` means the deterministic route is gone (or_bcd) or the source is
not public (ny_dos, FOIL) — the source stays active and the run stays red until it is resolved;
do not flip it to `queued` to get green.

## Changing the prompt
Edit `prompts/CLASSIFIER-PROMPT.md`. Run `python -m pipeline.run --layers 2-8` with class-B
rows present; G5 must pass on the seeds. The new hash lands in the release tag.

## Traps that cost days
- A stale `.~lock.*` file hangs every LibreOffice recalc. Near-zero CPU on a timeout is a lock.
- Dates written as strings never compare. Write date objects.
- Expanding-range COUNTIF is quadratic; use a fixed-range helper column.
- Say "IC = industrialized construction, not integrated circuits" in every model prompt.
