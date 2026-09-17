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

---

## Appendix: the truncation sweep, and its negative result

Two sources were found publishing a fraction of a roster as the whole roster on the same day —
sipa (one page of five, 10 rows against 14) and mbma (one screen of an infinite scroll). Both were
found by accident, so every archived list page was swept for the same failure rather than waiting
for a third.

410 archived HTML files across 10 sources, checked for pager links (`?page=`, `/page/N`, `start=`,
`offset=`, `pg=`) and for infinite-scroll and `rel="next"` markers, with blog, news, gallery and
event pagers excluded as irrelevant.

**Only sipa carries a real pager, and it is now followed.** Everything else is clean:
bldr_locations, corporate_locations, fl_bcis, iibc, in_dhs, mbi_members, mbma, pa_dced,
superior_walls. mbi_members was the one most worth checking — 52 letter pages, 188 rows — and none
of its letter pages carries a pager at all.

superior_walls flagged on a first pass and is a false positive worth recording so it is not chased
again: the pagination markup is on its blog and its photo gallery (`data-max-pages="5"` on an
`mk-gallery`), not on the licensee list.

The first pass of this sweep was itself wrong and is worth recording too. It stopped downloading
after six clean files per source, which meant it checked 10 of 410 files and skipped sipa's own
index — the one file that would have flagged. The second pass targets list-looking filenames with
no cutoff. A sweep that samples the wrong six files returns "clean" just as confidently as one that
checked everything.

---

## Run 25's actual result — v1.7, measured

    prompt 6127f4dbf7db · 18 sources · 13,057 candidates · all five gates pass
    UNCERTAIN 162   (baseline 366, v1.6 699, PREDICTED 403-447)
    recall 38.59%   located 30.71%   sealed 42.11%   published 2,726
    1,276,169 in / 839,911 out

**The UNCERTAIN prediction is falsified**: 162 against a predicted 403-447, and below even the v1.4
baseline of 366. v1.7 did not narrow the balloon back to its intended size, it collapsed it past
the starting point — which is the same over-reach the IC line showed live and which v1.8 fixes.

Recall at 38.59% beats run 22's 37.3%, and the attribution matters more than the headline. Run 25
carries three changes at once: v1.7, the dual-form matcher, and the three state registries.
Measured separately beforehand, the matcher alone took run 22 from 37.3% to 38.2% and the
registries reached 2 control rows. So the prompt's own contribution to recall is about one row, on
a run where the classifier's IC count fell roughly 150 below baseline.

That is the case for stopping, now with a number rather than a prediction: **three prompt versions
have moved recall by about one row.** 122 of the missing control rows are in no source, so they are
in no prompt's candidate pool, and the classifier cannot be the lever.

One correction to the figures this run reports: its `source_gap` reads a 49.4% ceiling because run
25 was dispatched from a commit predating the state-aware fix to `sources_holding`. The honest
ceiling is 44.4%.
