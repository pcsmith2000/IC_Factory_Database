# What EPA FRS actually reaches — and what it drops

Measured 2026-09-16 against `VALIDATED-LIST.md` (241 companies) and the archived EPA slice
(99,883 rows, already filtered to the NAICS families in `pipeline/sources/epa_frs.py`).

## Correction first

An earlier note in this work said only **15 of 240** validated companies appeared in the candidate
pool. That was wrong, and it was my measurement rather than the data: the matcher required the
city to agree and did not strip the OSHA establishment-number prefix that FRS puts on some names
(`317715012 - PACIFIC WALL SYSTEMS INC`). Matching on the name alone, against all 99,883 rows:

| | companies | share |
|---|---|---|
| Absent from the EPA slice entirely | **176** | 73% |
| Reach the classifier as candidates | **54** | 22% |
| In EPA, dropped before the classifier | **11** | 5% |

So the real figure is 54, not 15. The shape of the problem is unchanged, but its cause is not
what the wrong number implied.

## The dominant cause is absence, and it is not a bug

Three quarters of the companies you have validated are not in EPA FRS at all. That is what FRS
is: a registry of **environmentally regulated facilities**, not a registry of manufacturers. A
modular plant with no air permit, no hazardous-waste generation and no discharge has no reason to
appear in it. Autovol, Fading West, Plant Prefab and most of the newer volumetric builders are in
that position.

No amount of tuning reaches them. They come from the state registers, the industry rosters and
`corporate_locations` — which is exactly why the registry carries all three classes rather than
leaning on EPA.

## What we do drop, and why

Of the 99,883 rows in the slice:

| | rows |
|---|---|
| In a core NAICS code → candidate automatically | 2,266 |
| Keyword × NAICS family match → candidate via the name | 656 |
| **Neither → never reaches the classifier** | **96,961** |

97% is dropped before any judgement is applied, by `classify.candidates()`. A row survives only
if its NAICS is one of the four core codes, or its **name contains one of eleven keywords**
(`modular`, `prefab`, `panel`, `truss`, `precast`, `mass timber`, `sip`, `metal building`,
`component`, `volumetric`, `manufactured hom`) *and* its NAICS family is one the matrix pairs with
that keyword.

The eleven validated companies in EPA that never reach the classifier show what that costs:

| company | EPA name | NAICS | why it was dropped |
|---|---|---|---|
| Bankersteel (Orlando, Rock Hill) | `BANKER STEEL - ORLANDO` | 332312 | no keyword in the name |
| 84 Lumber | `84 LUMBER COMPANY` | 423310 | no keyword in the name |
| Shelter Systems | `SHELTER SYSTEMS` | 321211 | no keyword in the name |
| Toll Integrated Systems | `TOLL INTEGRATED SYSTEMS` | 321911 | no keyword in the name |
| Pacific Wall Systems | `PACIFIC WALL SYSTEMS INC` | 321918 | "wall systems" is not a keyword |
| North Georgia Truss Systems | `NORTH GEORGIA TRUSS SYSTEMS` | 236210 | **has** the keyword, wrong pairing |

That last row is the clearest: `truss` is in the matrix, but it is paired only with families
`3212` and `3219`. This plant is coded `2362`, so the pairing fails and a company on your own
validated list is discarded without ever being looked at.

The general failure is that **real manufacturers are often named after people and places**, not
after what they make. Banker Steel, 84 Lumber, Shelter Systems and Toll Integrated Systems are all
plants; none of them says so in its name.

## The dropped rows, by family

| family | dropped | what is in there |
|---|---|---|
| 2362 | 44,510 | commercial/institutional building construction — mostly genuinely not plants |
| 3273 | 19,759 | concrete products — infrastructure precast, but building precast too |
| 3323 | 12,815 | architectural and structural metals — metal building manufacturers live here |
| 3219 | 8,814 | **other wood product manufacturing — truss and panel plants live here** |
| 3211 | 4,532 | sawmills and wood preservation |
| 2381, 3272, 4233, 3212 | 6,531 | |

`3219` is the one to worry about. It is the family that holds the panel and truss plants this
database exists to find, and 8,814 of its rows are being discarded on the strength of their names.

## The recommendation: stop pre-filtering on names

Keyword pre-filtering is a false economy here. It exists to keep the classifier cheap, and the
classifier is not expensive: the measured cost is **$0.06 per 2,922 rows**. Classifying the whole
99,883-row slice would cost roughly **$2 per run** — for a job that runs quarterly, about $8 a
year, against a filter that is currently discarding known-good plants silently.

Options, cheapest first:

1. **Pair `truss` with `2362`** and add `wall system`, `building system`, `structural` to the
   matrix. Recovers the named cases. Narrow, safe, does not change what the run costs.
2. **Make every row in `3219` and `3212` a candidate** regardless of name — the two families
   densest in the plants we want. Adds ~10,000 rows, taking the classifier from 2,922 to ~13,000
   and the cost from $0.06 to ~$0.27 a run.
3. **Classify the whole slice.** ~$2 a run, and the name-based filter stops being a silent,
   untested judgement made before the judgement.

Option 2 or 3 also changes what G5 is measuring: the seeds were drawn from the current candidate
pool, and widening the pool widens the population the gate is meant to represent.

## One thing this does not fix

The frame in `registry/config.yaml` is four NAICS codes totalling 2,750 establishments, and that
is the denominator Layer 7 divides by. Widening candidate generation finds plants *outside* those
four codes, so coverage can rise above 100% without the database being complete. If the scope
really is broader than the four core codes, the frame should say so — otherwise the coverage
number quietly measures something other than what it claims.
