# Control — held out, checksummed, never merged

**A bare list of names is enough.** `name` is the only column recall needs: it matches on an
explicit crosswalk link first, then name+state where the row has a state, then on the name alone.
A row with no `triage` is treated as in scope and the count of those is reported. So dropping in
241 verified names with nothing else scores real recall — before 2026-09-17 it would have scored
0%, because the matcher required a state and a names-only list matched nothing.

`control-triaged.csv` — the 241-row validated list with a `triage` column:
`in_scope_locatable` · `in_scope_no_location` · `out_of_scope` (28 rows: trade association,
media, advisory, developer, foreign-only, no factory). Columns: `control_id,name,city,state,triage,reason`.

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

**v1.0 ships these as headers only.** The triaged list, seeds and frame totals are added from
the 2026-09-09 build in the data commit.
