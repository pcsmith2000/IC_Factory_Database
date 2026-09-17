# v1.9 — one code, one rule, written before the run

Registered 2026-09-17 before dispatch. Prompt hash `b361b8d772ab`. Diagnosed from run 35269300278's
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
