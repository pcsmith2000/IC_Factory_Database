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
classifier is not expensive: the measured cost was **$0.06 per 2,922 rows**. Classifying the whole
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

## What was done: option 2, 2026-09-16

`classify.WIDE_NAICS_FAMILIES = {"3219", "3212"}`. Every row in those two families is a candidate
regardless of what it is called; the core-NAICS and keyword × family rules are unchanged, and each
row still records why it was admitted in `_candidate_reason` (`wide family 3219`).

Measured against the same archived slice:

| | before | after |
|---|---|---|
| Core NAICS | 2,266 | 2,266 |
| Wide family (3219, 3212) | — | 10,172 |
| Keyword × family | 656 | 535 |
| **Candidates** | **2,922** | **12,973** |
| Cost per run, `openai/gpt-oss-120b` | $0.06 | **$0.27** |

The keyword count falls from 656 to 535 because 121 of those rows were in 3219 or 3212 already and
are now admitted on the family instead — the same rows, credited to the stronger reason.

EPA is the only active source with `needs_classify: true` (`osha_enforcement` is queued), so 12,973
is the whole pool, not the EPA share of it. `pipeline/bakeoff.py`'s full-run token constants were
re-measured to match; any full-run price quoted before this change is about a quarter of the truth.

### What it recovers, and what it does not

Recovered — in EPA, previously dropped, now candidates:

| company | NAICS | admitted as |
|---|---|---|
| Shelter Systems | 321211 | wide family 3212 |
| Toll Integrated Systems | 321911 | wide family 3219 |
| Pacific Wall Systems (3 records) | 321918, 321999 | wide family 3219 |
| 84 Lumber, Richmond door shop | 321911 | wide family 3219 |

Still dropped, and this is the honest limit of option 2 — all three are outside 3219 and 3212:

| company | NAICS | why |
|---|---|---|
| Banker Steel (7 records) | 332312 | fabricated structural metal; no keyword in the name |
| 84 Lumber (the 423310 and 2362 records) | 423310, 2362 | wholesale and project rows |
| North Georgia Truss Systems | 236210 | has `truss`, but the matrix pairs it only with 3212/3219 |

Option 1 was done next, in the same session — see below.

### Two consequences to keep in view

G5's 60 seeds were drawn from the 2,922-row pool. They now stand for a pool 4.4× larger and
differently composed — mostly unremarkable 3219 and 3212 rows the seeds do not represent. The gate
still guards every run, but its population and the run's have drifted apart; re-seeding from the
widened pool is the fix, and is not done.

And the frame is still four NAICS codes totalling 2,750 establishments, which is what Layer 7
divides by. This change deliberately finds plants outside those codes, so coverage can now exceed
100% without the database being complete. That is a scope decision — widen `frame.naics`, or say
plainly that coverage measures the four core codes only — and it is the user's, not a tuning knob.

## One thing this does not fix

The frame in `registry/config.yaml` is four NAICS codes totalling 2,750 establishments, and that
is the denominator Layer 7 divides by. Widening candidate generation finds plants *outside* those
four codes, so coverage can rise above 100% without the database being complete. If the scope
really is broader than the four core codes, the frame should say so — otherwise the coverage
number quietly measures something other than what it claims.

## What was done: option 1, 2026-09-16

`truss` now pairs with `2362`, and three product-name keywords were added: `wall system`,
`building system` and `structural`. On top of the widening, this takes candidates from 12,973 to
**13,117** — 144 rows, a rounding error on cost, so the run stays at ~$0.27.

| new rule | rows | what it finds |
|---|---|---|
| `structural` × 3323, 2362 | 89 | steel fabricators — Fritz, Barro, Ahlborn, Trinity |
| `building system` × 3323, 2362, 3273, 2381 | 35 | NCI, Ceco, Schulte, New Millennium, Clark Dietrich |
| `truss` × 2362 | 12 | Georgia Truss, Magbee Truss Plant, A1 Truss, Carnesville Truss Plant |
| `wall system` × 3323, 3273, 2381, 4233 | 8 | BASF, Superior, Miami, Engineered Wall Systems |

### Why `truss` × 2362 is only 12 rows

44 rows in 2362 carry the string `truss`. **32 of them are the town of Trussville, Alabama** —
a library, a mini-storage, a high-school stadium, a Kia dealership. `PLACENAME_COLLISIONS` catches
those before the keyword loop, which is exactly the job it was added for, and the 12 that survive
are almost all plainly truss plants. The guard is doing real work here; adding this pairing
without it would have been a precision disaster.

One row slips past it: `NEW DAY TRUSSVILE`, a misspelling the guard's exact-substring match
doesn't see. It reaches the classifier, which is where a judgement call belongs.

`structural` is the loosest of the four and was paired deliberately narrowly. Fabricated
structural steel for a building *is* a building system; for a bridge it is not, and the name alone
cannot separate them. 89 rows is a cheap price for putting that question to the classifier instead
of answering it with a regex.

### A correction to what option 1 was sold on

The options list above said option 1 would "recover the named cases" and "probably Banker Steel".
Measured, it recovers **North Georgia Truss Systems** and nothing else from the missed eleven.
Banker Steel's seven EPA records are named `BANKER STEEL - ORLANDO`, `BANKER STEEL SOUTH PLANT`
and so on: **there is no keyword in any of them**, so no name rule can reach them, and 332312 is
outside the two wide families. The same is true of 84 Lumber's 423310 record.

That is the standing limit of options 1 and 2 together. Both widen *which names and codes* get a
hearing; neither helps a plant whose name says nothing about what it makes. Only option 3 —
classify the whole 99,883-row slice, ~$2 a run — removes the name filter as a silent judgement.
Banker Steel is the concrete argument for it.

### Where candidate generation stands

| | rows |
|---|---|
| Wide family 3219 | 8,877 |
| Wide family 3212 | 1,295 |
| Core NAICS | 2,266 |
| `precast` × 3273 | 488 |
| All other keyword × family | 191 |
| **Candidates** | **13,117** of 99,883 (13%) |

Against 2,922 (2.9%) before either option.
