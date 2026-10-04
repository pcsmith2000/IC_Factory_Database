# Duplicate merge agent: research each candidate pair, then merge or reject it

You resolve duplicate facilities in ADL Ventures' IC Factory Database: two IC-numbers that are the
same physical plant. A missed duplicate shows a plant twice; a **false merge loses a real plant**,
which is worse. So you merge only on evidence, and you leave anything uncertain for a person.

Run id: **`wr-dupes-1`**. Reuse it every time you resume.

## 0. Rules

- Read `CLAUDE.md` in this repo first. Its warehouse rules bind you.
- **Reads:** Neon, read-only (`python -m pipeline.neon_sql "SELECT …"`, or the Neon connection you
  were given, inside `BEGIN READ ONLY; … COMMIT;`).
- **Merges** happen only through the `duplicates.yml` GitHub Actions workflow on `main`, never by
  SQL. Always a **dry run first** with the exact `only` list, read its JSON, then the real run with
  the same list. Never `apply` a whole tier without an `only` list.
- **Rejections** ("not the same plant") are the one SQL write you may make, one pair at a time,
  stating the SQL first:
  ```sql
  UPDATE facility_duplicate_candidate
     SET status = 'rejected',
         decided_at = to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
         decided_by = '<agent name> (wr-dupes-1)'
   WHERE status = 'pending' AND facility_id = 'IC-A' AND duplicate_of = 'IC-B';
  ```
  Reject **both directions** when a pair is queued twice (A→B and B→A).
- **New pairs** you find that are not queued: submit a `duplicate` verdict to
  `web_research_submission` (§4). Ingest queues it as a candidate; you merge it on a later pass.
- The warehouse workflows share one concurrency group and GitHub keeps one pending run: **wait for
  each run (and the golden refresh it triggers) to finish before dispatching the next.**
- Never print or commit connection strings, passwords or keys.

## 1. The work list

Pending candidates, plants on the Volumetric page first:

```sql
BEGIN READ ONLY;
SELECT c.facility_id, c.duplicate_of, c.tier, c.source, c.evidence,
       a.name AS a_name, a.address AS a_addr, a.city AS a_city, a.state AS a_state, a.website AS a_web,
       a.phone AS a_phone, a.lat_lon AS a_pin, a.capability_leaf AS a_leaf,
       b.name AS b_name, b.address AS b_addr, b.city AS b_city, b.state AS b_state, b.website AS b_web,
       b.phone AS b_phone, b.lat_lon AS b_pin, b.capability_leaf AS b_leaf
  FROM facility_duplicate_candidate c
  JOIN golden_facility a ON a.facility_key = c.facility_id
  JOIN golden_facility b ON b.facility_key = c.duplicate_of
 WHERE c.status = 'pending'
 ORDER BY (a.capability_leaf IN ('Wood Volumetric Modular','Steel Volumetric Modular')
        OR b.capability_leaf IN ('Wood Volumetric Modular','Steel Volumetric Modular')) DESC,
          c.tier, b.state, b.city;
COMMIT;
```

Also look for pairs nobody queued: two live rows in one state with the same street number in the
same city, the same 10-digit phone, the same website domain, or the same pin.

## 2. Research one pair (about 5 minutes, 3 to 6 queries)

Answer: **is this one physical plant, or two?** Sources, best first: the company's own locations /
factories / contact pages; state modular or HUD programme listings (they list each plant
separately, with plant numbers); Secretary of State and SEC filings; news of an acquisition or
closure; map listings, which are weakest.

| Finding | Decision |
|---|---|
| Same plant: same street address (any spelling: "East … Road" = "E. … Rd."), and the names are the same firm, a d/b/a, or an owner and its plant | **merge** |
| Same site, new operator (a plant sold or taken over) | **merge**, and note the current operator; the survivor's name may need a person's ruling (§5) |
| Numbered or named separate plants of one company ("Plant #1" / "Plant #2", "Plant 2" / "Plant 3"), even on one campus or sharing a phone and website | **reject** |
| Sister companies or brands that share a website or phone but have different plant addresses | **reject** |
| One row is an office, HQ or sales lot and the other the plant | not a merge: **reject**, and say which row is not a plant (§5) |
| Sources conflict, or you can't tell | **leave pending**, write one answerable question |

Shared phone or website alone is never enough: one corporate number serves many plants.

## 3. Merge in batches

Collect the pairs you decided to merge, then:

1. Dispatch `duplicates.yml` on `main` with `command=apply`, `tiers=<the tiers of your pairs>`
   (comma-separated: `certain,likely,review`), `only=<comma-separated facility_id list>`,
   `dry_run=true`.
2. Read the run's JSON (`merges`, `skips`). Every merge must be a pair you decided on, in the
   direction you expect (the survivor is `into`). If anything else appears, stop and fix the list.
3. Dispatch the same inputs with `dry_run=false`. Wait for it and the golden refresh to finish.
4. Verify: `SELECT facility_id, status, merged_into FROM facility WHERE facility_id IN (…)` shows
   `merged`, and the merged ids are gone from `golden_facility`.

Batches of up to about 30. If a facility the BR-IC/MCI rule names (`NOT_HOUSING` in ADL_Viz
`src/lib/navy-ready.ts`) would be merged *away* (not the survivor), stop and report it: the rule is
keyed by IC-number.

## 4. Proposing a pair nobody queued

```sql
INSERT INTO web_research_submission (submission_id, facility_id, run_id, agent, submitted_at, payload)
VALUES ('wr-dupes-1:IC-A', 'IC-A', 'wr-dupes-1', '<agent name>', now()::text, $json$
{"facility_id": "IC-A", "agent": "<agent name>", "run_id": "wr-dupes-1",
 "verdict": {"status": "duplicate", "duplicate_of": "IC-B",
             "reason": "Same plant: <address> per <source>; <why the names are one firm>",
             "source_refs": ["s1"], "confidence": 0.8},
 "sources": [{"source_ref": "s1", "url": "<exact page>", "title": "<title>",
              "kind": "company_site | government_registry | filing | news | trade_directory | map_listing",
              "found_by": "<agent name> via <search>", "retrieved_at": "YYYY-MM-DD"}],
 "assertions": []}
$json$)
ON CONFLICT (submission_id) DO NOTHING;
```

`duplicate_of` is the row that should survive: normally the one with more sources, a pin, and a
registry behind it.

## 5. What you don't do

Renaming a plant, marking a row an office (`existence_flag`), or changing a capability is a
person's ruling (`control/operator_assertions.csv`). List those in your report with the evidence;
don't make them.

## 6. Report back after each batch

- merged: `IC-A → IC-B` (name, city) and the one-line evidence;
- rejected: the pair and why (e.g. "Plant #1 and Plant #2 are separate plants per Indiana DHS");
- left pending: the pair and the one question a person must answer;
- proposed new pairs;
- rulings for a person: renames for a new operator, office/HQ rows, capability errors you saw.
