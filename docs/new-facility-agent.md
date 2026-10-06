# New facility agent: add a plant no source lists, by pull request

You add a manufacturing plant to ADL Ventures' IC Factory Database that is real, has capacity for
industrialized construction, and is **not in the database yet**. You do it with one line in
`control/new_facilities.csv` and a pull request. A person reviews and merges it; the warehouse then
mints the plant's permanent IC-number and brings it into golden. You never write SQL for this.

A missing plant costs comprehensiveness; a wrong or duplicate plant costs veracity, which is worse.
Add a plant only when you can cite where it is and what it makes.

## 0. Rules

- Read `CLAUDE.md` first. Its warehouse rules bind you. Reads only (`python -m pipeline.neon_sql "…"`).
- One pull request may add several plants, one line each. Never edit or delete a merged line except to
  correct it (change the value and the `retrieved_date`); never reuse an `intake_id`.
- No coordinates read off a map. Leave `lat_lon` blank unless the company's own page or the
  Census geocoder (`geocoding.geo.census.gov`, rooftop/address match) gives it; say which in `evidence`.
- A plant the database already holds is **not** a new plant: fix its facts with
  `control/monitor_fix_assertions.csv` (contact/location) or report them for a person's ruling.

## 1. Make sure it is not already there

Search golden by name, then by address, then by phone and website domain:

```sql
BEGIN READ ONLY;
SELECT facility_key, name, address, city, state, zip, website, phone, capability_leaf
  FROM golden_facility
 WHERE name ILIKE '%<distinctive word>%'
    OR (state = '<ST>' AND (address ILIKE '<street number> %' OR city ILIKE '<city>'))
    OR website ILIKE '%<domain>%'
    OR regexp_replace(phone, '\D', '', 'g') LIKE '%<10 digits>%';
COMMIT;
```

Also check the plant was not ruled out: `SELECT * FROM fact_assertions WHERE field_key = 'existence_flag'
AND value IN ('not_ic','closed') AND facility_key IN (…)`. If a hit is the same plant (same site, even
under a new owner), stop: it is a correction, not a new plant. If a hit is a *different* plant
(another plant of the same company, a sister brand at another address), list its IC-number in
`not_duplicate_of` and say why in `evidence`.

## 2. Establish the facts (cite every one)

| Column | Rule |
|---|---|
| `intake_id` | `NF-` + lower-case slug of name, city, state: `NF-custom-touch-homes-madison-sd` |
| `name` | The plant's operating name as the company writes it |
| `address`, `city`, `state`, `zip` | The **plant's** street address (not an office, PO box or registered agent). `state` is the two-letter code |
| `website`, `phone` | The company's, if published; blank otherwise |
| `capability_group`, `capability_leaf` | A leaf of `registry/taxonomy.yaml` and its group, from what the company says it builds. If you can't tell wood from steel volumetric: `Modular (type not determined)` |
| `sq_ft` | Plant floor area only if a source states it; digits only |
| `lat_lon` | Usually blank (§0) |
| `retrieved_date` | Today, `YYYY-MM-DD` |
| `issue` | The GitHub issue or this pull request (`#140` or its URL) |
| `evidence` | The URL(s) and what each shows: that the plant exists at that address and what it makes |
| `proposed_by` | `<agent name> (<run id>)` or the person's name |
| `not_duplicate_of` | IC-numbers you checked and found to be different plants, `;`-separated; else blank |

Best sources: the company's own plant/locations page; a state modular or HUD programme listing (they
list each plant); a certification body (IIBC, PFS, NTA); a news report of the plant opening. A map
listing alone is not enough.

## 3. The pull request

1. Branch from `main`, append your line(s), run `python -m pipeline.facility_intake --check` (and
   `pytest -q tests/test_facility_intake.py tests/test_control.py`).
2. Open the PR titled `New facility: <name>, <city> <ST>`. In the body, one paragraph per plant: what it
   is, the evidence, and the duplicates you checked.
3. The `facility-intake` check dry-runs your lines against the live warehouse. If it **refuses** a
   line, golden already holds a plant with that name in the state or that address in the city: open the
   IC-number it names. Same plant → drop your line. Different plant → add it to `not_duplicate_of`.
4. A person merges. On `main`, `facility-intake` mints the IC-number(s) (shown in the run summary) and
   `golden-refresh` brings them in within minutes. Report the IC-numbers back.

## 4. After it is in

The plant has no pin unless you gave one. A person sets it with **Correct location** on the map, and
the building review pane attaches its buildings. Later sources that list the plant (a registry, ADL's
lists) override your founding facts automatically; people's rulings override everything.
