# v1.8 — written before the run, and an argument for stopping after it

## v1.7's verdict

Run 35262458992, read at batch 56 of 132 against run 22's own per-batch spread (4,000 resamples of
its 132 cached batches; sd ~70 on an IC projection at that depth, ~55 on UNCERTAIN):

| quantity | v1.4 baseline | v1.6 final | v1.7 projected | v1.7 predicted |
|---|---|---|---|---|
| IC | 1,969 | 2,112 | **1,814** | 2,100–2,195 |
| UNCERTAIN | 366 | 699 | **196** | 403–447 |

IC is ~4 sd below its own predicted band and ~2 sd below baseline. **Falsified, in the direction
v1.7's own doc named as the expensive one**: "IC falling below 2,100 means the else-branch is
eating real off-code plants… the failure that costs recall rather than review time."

The cause is placement. "Off a core code, a hedge is not UNCERTAIN" sits directly under the
numbered test and reads as a fourth rule over every off-core record, so "label it NOT-IC" reached
records whose reason named a building product. Rule 1 is scoped to core codes because its
*mechanical form* is; the judgement it encodes never was.

## The change

The paragraph now opens by stating the IC path is untouched — a reason naming a building system or
component is IC, core code or not — and closes with the invariant: **this rule moves rows out of
UNCERTAIN, never out of IC. If applying it would change an IC label, you have misread it.**

## The prediction

- **IC returns to 1,950–2,200.** Restoring the off-core IC path should recover the ~150 rows v1.7
  lost against baseline. Anything still under 1,900 means the else-branch cannot be contained by
  wording and belongs in Layer 3 code.
- **UNCERTAIN stays low, 150–450.** The v1.7 scoping was right and is unchanged. If it jumps back
  toward 699 the two rules are entangled and only one of them can be had.
- **G5 seed recall ≥85%; audit floor near 0.5%.**
- **Recall at or above v1.4's 37.3%**, with the review queue roughly half its v1.4 size. That — same
  recall, half the human triage — is the only real gain available here, and it is worth one run.

## Why this should be the last prompt run

Three versions and roughly 3.5M tokens have produced no recall gain over v1.4:

    v1.4  IC 1,969  UNCERTAIN 366  recall 37.3%
    v1.6  IC 2,112  UNCERTAIN 699  recall 36.9%
    v1.7  IC ~1,814 UNCERTAIN ~196 recall pending

v1.6 raised IC by 143 and recall went *down*. IC count and control recall are only loosely coupled,
and the ceiling analysis says why: **122 of the 151 missing control rows are in no source we hold**,
so they are not in the candidate pool for any prompt to label. The classifier-recoverable set is
about 7 rows. The ceiling on today's sources is 49.4%.

If v1.8 lands at or above 37.3%, take it and stop. If it lands below, revert to v1.4's prompt
(hash 23e69d2b0d70) and stop anyway. Either way the next hours belong to sources and to the
per-row enrichment pass, not to this file.
