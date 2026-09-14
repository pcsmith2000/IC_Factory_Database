# Runbook

## A normal quarter
1. `run.yml` fires. Watch it. Green → a release PR appears.
2. Download the workflow artifact. Review `dedupe_audit_<date>.csv`: fill the `decision`
   column (merge / keep / co-located). Review `review_queue.csv`: decide each UNCERTAIN row.
3. Commit the decisions to `control/` (they become seeds and crosswalk entries) and merge the PR.
   The count in the run record's `release.published_count` is the quotable number, with its tag.

## A gate fails
- **G1 over 2%** — look at the pairs. If they are city-string variances, the fix is
  `norm_city` in `pipeline/contract.py`, not name matching. Re-run from layer 2.
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
Add an entry to `registry/sources.yaml` with class, method, `needs_classify`, traps and
`status: queued`. Write `pipeline/sources/<id>.py` from `_template.py`. Flip to `active`.
It is pulled next run. Nothing else changes.

## Changing the prompt
Edit `prompts/CLASSIFIER-PROMPT.md`. Run `python -m pipeline.run --layers 2-8` with class-B
rows present; G5 must pass on the seeds. The new hash lands in the release tag.

## Traps that cost days
- A stale `.~lock.*` file hangs every LibreOffice recalc. Near-zero CPU on a timeout is a lock.
- Dates written as strings never compare. Write date objects.
- Expanding-range COUNTIF is quadratic; use a fixed-range helper column.
- Say "IC = industrialized construction, not integrated circuits" in every model prompt.
