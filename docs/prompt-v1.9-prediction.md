# v1.9 — one code, one rule, written before the run

Registered 2026-09-17 before dispatch. Prompt hash `9118376580c2` (the 321214 rule alone hashed b361b8d772ab; the name-echo in the output contract below is in the same version). Diagnosed from run 35269300278's
classifier cache (v1.8, `dd4369b1ffae`) joined to the EPA FRS rows by row_hash; the control list
was consulted only afterwards, to say which of its rows this touches.

## The population

436 EPA rows carry NAICS 321214 — truss manufacturing, the one core code that names a single product.
Under v1.8 they were labelled:

    IC 363   NOT-IC 66   UNCERTAIN 7

Reading the 66 NOT-IC reasons:

| the reason … | rows | v1.9's answer |
|---|---|---|
| leans on a trading word: "supply", "dealer", "distributor", "lumber", "contractors" | 21 | IC — on 321214 the code is the product; these are Builders FirstSource, 84 Lumber, Carter Lumber, HD Supply, A-1 Truss, American Builders Supply, Carpenter Contractors of America |
| names a genuinely different product: pallets, treatment, machinery, a government office, a parcel | 15 | NOT-IC, unchanged |
| hedges or calls the name generic: "Components.", "Generic name truncated core code", "unspecified" | 6 | IC or UNCERTAIN — "components" on 321214 is truss components; a hedge on a core code is rule 3 |
| everything else (commodity lumber, "not core code", fabrication) | 24 | mostly unchanged; "SHOFFNER INDUSTRIES … not core code" on 321214 is the model misreading the truncated-code paragraph and should flip |

## The prediction

On the same candidates, same model, temperature 0:

- **321214 NOT-IC falls from 66 to 20–30**; 321214 IC rises from 363 to roughly 395–410.
- **Total IC moves by +30 to +50.** That is inside two standard deviations of run-to-run noise
  (±27 sd on the count), so the aggregate is NOT the check; the 321214 slice is. Read the verdict
  on the slice.
- **UNCERTAIN barely moves** (+0 to +6 from the hedged six). If UNCERTAIN rises by more than ~20,
  the rule has been read as "when in doubt on 321214, UNCERTAIN", which is not what it says.
- **Control:** up to four rows become findable — PDJ Components (Chester NY, "Components."),
  S.R. Sloan (Whitesboro NY, "Generic name truncated core code"), American Builders Supply
  (Sanford FL is on 321214; the control's Winter Haven and Tampa plants are not in FRS, so at most
  one matches by name+state), and A-1 Truss (Fort Pierce FL, if the control's "A-1 Industries of
  Florida" is the same plant — the name rung may not bridge it). +1 to +1.7 points on 239.
- **G5 seed recall holds.** The rule widens one code; nothing on the seeds is on 321214 with a
  dealer name that should be NOT-IC.

## What would falsify it

321214 NOT-IC staying at 50 or more means the Dealers paragraph still wins and the exception has to
move up into the mechanical test itself. IC rows appearing on 444190 or 423310 with dealer names
means the rule leaked off its code — it is written to one code precisely so that cannot happen, and
if it does, the prompt is being read by name-pattern rather than by code. A rise in UNCERTAIN of
more than ~20 is the third failure mode above.

Stop-rule note: this is a prompt change after "no further prompt edits" was written under v1.8. The
difference is that v1.5–v1.8 were sized on the whole population and judged on the aggregate; this
one is a 436-row slice with a 21-row defect the reasons name themselves, and it is judged on the
slice.

## Shipped with it: labels anchored on the echoed name, not the index

Found while reading run 28's G5 halt. Run 28 missed 6 of 30 IC seeds where run 27, same prompt,
missed 2 — and two of the six carried reasons about a different establishment. Cavco Industries
(321991, an IC seed) was NOT-IC because "Pallets are excluded"; in the same batch, Pallet One of
Alabama was IC because "Formetco makes metal buildings", and Formetco carried Roseburg's "Wood
products supply". In run 27's batch 4572e36f, 99 rows came back with 94 distinct `i` values and
every reason from i=62 on belonged to the row five places later (Herrick Mill Work: "Larocco
Architectural millwork"; United Structures of America: "Washington Lumber dealer").

The model skips a row and then numbers by its own count. Mapping by echoed index — the previous
rule — cannot see that, because every index after the skip is well-formed and wrong. So the
output contract now includes `name`, copied as given, and `pipeline.classify._anchor` attaches each
object to the row whose name it echoes: at `i` when they agree, at the one other row that carries
the name when they do not (`realigned`), dropped and re-asked when the name matches nothing or
several (`misanchored`). An object with no name at all is taken at its index as before
(`unanchored`), so a model on the old contract loses nothing. All three counts go on the heartbeat
and into the run record.

Predictions, in addition to the 321214 ones above:

- `realigned` + `misanchored` per run is in the tens to low hundreds, not zero and not thousands.
  Zero means the model never slips (contradicted by two runs' caches) or is not echoing names
  (then `unanchored` is ~13,000 and the transport line needs changing). Thousands means the
  anchor key is too strict and is rejecting names that are the same.
- Seed misses caused by slippage — Cavco's kind — do not recur. A G5 miss now carries a reason
  about the seed itself.
- The run-to-run label flip rate, last measured at ~8% between two v1.4 runs, falls. That is
  measurable only with two runs on this prompt, and is the number to report when they exist.


## Verdict — run 35276704513, read against the predictions above

    321214 slice:   run 27  IC 363 · NOT-IC 66 · UNCERTAIN 7
                    run 30  IC 411 · NOT-IC 25 · UNCERTAIN 0
    predicted:      NOT-IC 20-30, IC 395-410       -> both inside or a hair above the range

Of the 21+ trading-word NOT-IC rows named in the diagnosis, 27 of 30 are IC now and 3 stay
NOT-IC. UNCERTAIN did not balloon (159 in total across 16,606 candidates, against 131 on 13,057).
Control: PDJ Components, S.R. Sloan and American Builders Supply are found — three of the four
named as reachable; A-1 Truss did not bridge to "A-1 Industries of Florida", as the prediction
allowed. G5: precision 97%, recall 97% on the 60 seeds — the best of any run, against 92/80 on
run 28 where slipped labels halted it.

**Anchoring:** `realigned` 61, `misanchored` 4, `unanchored` 0 across 167 batches. Inside the
predicted "tens to low hundreds"; the model echoes every name, and 61 objects were sitting on the
wrong index and are now on the right establishment. Re-asks 10.

**The widened slices, which were not a prediction but are a cost to account for:** steel-named
3323 rows, 1,121 newly classified -> 79 IC (Valley Joist, Quincy Joist, Carolina Steel Group,
Skyline Steel...) and 1,041 NOT-IC; 327390 whole, 1,724 newly classified -> 39 IC (Clark Pacific
x2, Coreslab x2, Rocky Mountain Prestress, Southeastern Prestressed, Structurecast) and 1,672
NOT-IC. 118 plants for ~28 batches. Clark Pacific - Woodland is found (T1) because of it.

Run-level: recall 41.0% -> 42.3% (101 of 239); located 35.6% -> 40.2%; sealed 16 -> 17 of 38;
T2+T3 624 -> 720 (+15%). All seven enriched leads from the first lead_addresses batch came in
located, five of them at T2/T3 — the enrichment street and a second source agreed. Two rows were
lost: 84 Lumber (its run-27 match was the Austin door shop, a false match now correctly refused)
and True Volumetric Corp (Ontario; the scope question is still Peter's).
