# v1.5 — what it should do, written before the run

Registered 2026-09-17, before dispatching the prompt change, so the result can contradict it.
Diagnosed from run 35243029519's classifier cache against the EPA population; the control list was
not consulted, and the sealed quarter of it is the check that this was not fitted.

## The population

2,266 EPA rows carry a core NAICS code (321214, 321991, 321992, 332311). 607 of them — 27% — were
labelled NOT-IC under v1.4. Classifying those 607 by what their own stated reason says:

| the reason … | rows | v1.5's answer |
|---|---|---|
| hedges: "unclear", "likely", "may be", "without specifics" | 77 | UNCERTAIN, not NOT-IC — a hedge is the definition of uncertain |
| leans on a trading word: "supply", "dealer", "contractor" | 73 | not a product; cannot override a core code |
| names an IC product anyway: "metal building products", "truss and building supply" | 113 | that reason is the evidence FOR it |
| names a genuinely different business: staffing, food, pipe, RVs, carports | 83 | correctly dropped; v1.5 changes nothing |

| says it makes components rather than whole buildings | 21 | a scope error — two core codes ARE component codes |

Union of the first three: **226 rows, 37% of the 607.** The component bucket overlaps them and is
small, but it is the one that bears hardest on the control list, which is 38% structural
components; of the 21, Trachte (a metal building manufacturer, twice) and Weyerhaeuser (engineered
structural members) are plainly wrong, while Chicago Tube & Iron, Hydro Extrusion, Air Vent and
J.I.G. Machine Works are plainly right and turn on the product, not the word "component".

## The baseline, pinned before the comparison

Run 35255141179 (v1.4, prompt 23e69d2b0d70, 15 sources, same 13,057 candidates) labelled:

    IC 1,969   UNCERTAIN 365   NOT-IC the rest

The three v1.4 runs before it read IC 2,017 / 1,964 / 2,012, so 1,969 is squarely in that band
and the classifier is reproducible at temperature 0 to within about 3%. Any v1.5 movement larger
than that is the prompt, not noise.

## The noise floor, measured — read the verdict against this, not against zero

Before trusting any v1.5 diff, promptdiff was run on TWO v1.4 RUNS — runs 35243029519 and
35255141179, same prompt hash 23e69d2b0d70, same model, temperature 0, same 13,117 rows:

    moves: NOT-IC -> IC 292 · NOT-IC -> UNCERTAIN 253 · IC -> NOT-IC 234
           UNCERTAIN -> NOT-IC 212 · UNCERTAIN -> IC 29 · IC -> UNCERTAIN 18
    dropped 252  gained 321
    core IC NAICS: -161 +200 = net +39

**About 8% of individual labels flip between identical runs.** The aggregate is stable only
because the flips very nearly cancel: net IC across four v1.4 runs reads 2,017 / 1,964 / 2,012 /
1,969, a spread of 53 around a mean near 1,990.

This corrects something stated in the previous commit. "The classifier is reproducible at
temperature 0 to within about 3%" is true of the COUNT and false of the ROWS, and the distinction
matters here: no single row moving from NOT-IC to IC in the v1.5 run is evidence of anything, and
a row-level list of "plants v1.5 recovered" would be roughly a quarter noise.

So the verdict is read on the aggregate, against these floors:

| quantity | run-to-run noise (v1.4 vs v1.4) | v1.5 must beat |
|---|---|---|
| net IC count | ±27 (1 sd of four runs) | +135 to +226 predicted, 5-8 sd |
| net core-NAICS | +39 | +226 predicted, ~6x |
| gross row moves | ~1,038 | not usable as evidence at all |

## The prediction

On the same 13,057 candidates, same model, temperature 0:

- **IC labels rise from 1,969 to roughly 2,100–2,195** (+135 to +226). Not all 226: the buckets overlap, and
  some are right for reasons the regex cannot see (Eklof Docks, Johnson Controls Fire Protection
  and CertainTeed Ceilings are all correctly NOT-IC despite naming a building product).
- **UNCERTAIN rises from 365 to roughly 403–442** (+38 to +77), into the review queue where a human settles
  it. UNCERTAIN going up is a success, not a cost.
- **G5 seed recall holds at or above the 85% floor.** This is a widening, so seed recall should
  rise or hold; if it falls, the widening is admitting the wrong rows and the gate should halt the
  run, as it did for v1.3.
- **The audit floor (known-non-IC keyword) does not move much.** It sat at 0.5%; a widening that
  pushes it back toward v1.1's 9.5% has reopened the door v1.2 closed.

## What would falsify it

IC labels barely moving means the model was never reading the rule that changed, and the fix is in
how the instruction is placed rather than what it says. A large IC rise with G5 recall falling, or
the audit floor climbing, means v1.5 widened past the boundary rather than enforcing it — in which
case the 90 self-contradicting rows need naming individually rather than by rule.
