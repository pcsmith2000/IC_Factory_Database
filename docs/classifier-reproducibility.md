# How reproducible is the classifier, and what that costs

Measured 2026-09-17 on runs 35170703333 and 35174109197: same prompt (`275e1a45a6be`), same model
(`inception/mercury-2.5`), same 13,117 candidate rows, `temperature: 0`.

| | |
|---|---|
| identical labels | 11,493 of 13,117 = **87.6%** |
| disagreements | 1,624 |
| IC → NOT-IC | 578 |
| NOT-IC → IC | 592 |
| IC count | 2,228 vs 2,232 (**+4**) |

Temperature 0 is not determinism. One row in eight gets a different label on a second pass, and
the disagreements very nearly cancel, so aggregate rates are stable while row-level membership is
not. Anything computed as a *difference between two runs* inherits this.

## What it invalidates

Prompt versions were compared by diffing two runs. The noise floor of that comparison, from the
same-prompt pair above, is:

```
core IC NAICS   -231 +199 = net  -32
product NAICS                   -281
```

Against the prompt changes actually measured:

| comparison | core net | verdict |
|---|---|---|
| same prompt (noise) | −32 | — |
| v1.1 → v1.2 | −90 | ~3x noise: suggestive, not established |
| v1.2 → v1.3 | +94 | ~3x noise: suggestive, not established |

A single pair gives one sample, not a standard deviation, so "3x the one observed noise value" is
weak evidence. **The claim that v1.3 recovered a net +94 core-code plants does not survive this,
and neither does v1.2 having lost 90.** Both were reported as measurements during the 2026-09-17
session and should be read as consistent-with rather than demonstrated.

Named cases are firmer than counts: Deltec Homes and Pacific Wall Systems went NOT-IC under v1.2
and IC under v1.3, and those are specific, checkable plants rather than a net.

## What survives

Rates over the whole admitted population, not differences between runs:

| | v1.1 | v1.2 | v1.3 |
|---|---|---|---|
| admitted names matching known non-IC brands/product words | 9.5% | 0.2% | 0.3% |
| admitted rows in building-product NAICS families | 26.1% | 4.0% | 4.5% |

A 30x fall on one estimator and 6x on an independent one, across ~1,500-2,200 admitted rows, is
far outside anything ±1,600 flipped labels can produce — especially as the flips cancel. The
building-products boundary did something real.

## What it means for G5

G5 scores 30 IC seeds, so one label is 3.3 points. On the same-prompt pair the gate reported:

| run | precision | recall |
|---|---|---|
| 35170703333 | 100% | 87% |
| 35174109197 | 96% | 90% |

**±3 points on identical configuration.** The gate's own reproducibility is the same order as the
differences it is being asked to adjudicate — v1.2 at 87% and v1.3 at 83% is a 4-point gap against
a 3-point noise band. Run 35177447708 halting at 83% against an 85% floor may be a real regression
or may be a draw from that band, and 60 seeds cannot tell the difference.

This is not an argument for lowering the gate. It is an argument that the seed set is too small to
adjudicate prompt versions, which `registry/config.yaml` already anticipates: *"Revert to
0.95/0.90 once the seed set is re-drawn from the current 13,117-row pool and grown to 200-300, at
which point a label is worth ~0.4 points."* At n=300 a label is worth 0.33 points and the gate can
resolve what it is currently being asked to.

## If a decision is needed sooner

- **Score G5 over k passes** and require the mean to clear the bar. Cost is k× the seed batch,
  which is cents, and it collapses the variance without touching the threshold.
- **Judge prompts on population rates** (the table under *What survives*), not on row diffs, until
  the seed set grows.
- **Do not re-run a failing prompt hoping for a better draw.** That is selecting on noise, and it
  is what this document exists to make visible.

## G3 on the classified path

G3 requires identical inputs to issue zero new ids. With 1,624 rows changing label between runs,
the IC set — and therefore the clusters and their signatures — genuinely differs, so a `--rerun`
on the classified path can fail G3 without any defect in the id logic. G3 is meaningful on the
deterministic path (`IC_AI=off`), where it has been exercised and passes; on the classified path
it is testing the classifier's reproducibility, not the registry's.
