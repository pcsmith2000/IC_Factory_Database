# Control — held out, checksummed, never merged

**A bare list of names is enough.** `name` is the only column recall needs. Rungs, strongest
first: an explicit crosswalk link · name+city+state · name+state · the name alone · a whole-word
name prefix within the same state ("Fading West" is the list's name for FADING WEST BUILDING
SYSTEMS, LLC). The prefix rung needs the shorter name to be two tokens or eight characters —
one word matched far too much ("Blue Company" normalises to "blue" and took "Blue Horse
Building") — and never crosses a state, which is what keeps 84 Lumber's Virginia row off its
Bessemer, Alabama door shop.
A row with no `triage` is treated as in scope and the count of those is reported. So dropping in
241 verified names with nothing else scores real recall — before 2026-09-17 it would have scored
0%, because the matcher required a state and a names-only list matched nothing.

`control-triaged.csv` — the ADL list, 241 rows, placed 2026-09-17. Columns:
`control_id,name,city,state,triage,reason`; `reason` carries the product category the list
assigned (79 Wood Structural Components, 23 SIP/ICF, 18 Wood Volumetric Modular, 12 Mass Timber,
12 Steel Structural Components, 8 Steel Volumetric Modular, and so on; 43 rows are a bare name).

**`triage` is blank on all 241 rows and the name is now honest about it.** An earlier version of
this file said 28 rows were `out_of_scope` — trade association, media, advisory, developer,
foreign-only, no factory. Nobody had done that triage; the number was written next to an empty
file. Layer 7 counts an untriaged row as in scope and reports the count, so recall measures
today against all 241. Triage narrows the denominator; it does not turn a miss into a hit.

**The list is PLANT-level, not company-level.** Builders FirstSource is 21 rows in 13 states,
The Truss Company is 5, 84 Lumber and Universal Forest Products are 4 each — 199 distinct names
across 241 rows. Recall therefore matches one-to-one: a warehouse facility satisfies at most one
control row, and a row whose company is present but whose plant is not is reported separately as
`company_present_plant_missing` rather than counted as found.

A copy lives in the blob at `ic-control/adl-control-2026-09-17.csv` — deliberately NOT under
`ic-sources/`, which is the only prefix Layer 1 reads. The control set is held out; putting it
where an acquirer could pick it up would make G4 a formality.

`control.sha256` — SHA-256 of `control-triaged.csv`. Gate G4 fails if it does not match.
Re-triage is a logged edit: change the file, update the checksum in the same commit, say why.

`crosswalk.csv` — `control_id,row_hash,facility_id,legal_entity_id`. Explicit links between
control rows and database rows. Provenance beats inference: re-deriving links by string
similarity understated recall by 21 points.

`seeds.csv` — the seeded eval set for G5. Contract columns plus `seed_label` (IC / NOT-IC),
~30 of each, hidden in every classifier batch. A seed that turns out to be wrong (the resort
and marina) is corrected here, with a note, not silently dropped.

`frame_state_totals.csv` — `state,establishments` from Census CBP for the four core codes,
state level only. The denominator. Refresh annually and record the vintage in config.

**v1.0 shipped these as headers only.** Seeds and frame totals were added from the 2026-09-09
build; `control-triaged.csv` was filled on 2026-09-17 with the ADL list.
