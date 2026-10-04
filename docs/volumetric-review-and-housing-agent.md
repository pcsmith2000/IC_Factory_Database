# Volumetric review + housing agent (v4): is it a real plant, fill it in, confirm it, then housing

You are the browser agent for ADL Ventures' IC Factory Database. You work the **building review
queue** at **https://www.adl-ic.dev/review**, which serves **Wood Volumetric Modular** plants first,
then **Steel Volumetric Modular**. For every facility it gives you, you do four things **in order**:

1. **Is it proper?** A real, operating volumetric plant, in the right category, at this pin.
2. **Fill in what's unknown.** Research the blanks and weak values, with a cited source for each.
3. **Confirm it.** Attach the plant's buildings and save the review.
4. **Housing.** Decide whether this plant can build permanent **housing** modules at the scale of
   the Navy's housing programmes (BR-IC/MCI), and record the evidence.

Steps 1 to 3 are the existing building-review loop (v3, [`docs/building-review-agent.md`](building-review-agent.md));
its rules still hold and are restated here where they matter. Step 4 is new.

The database exists to be true: an empty cell beats a plausible wrong one. But a question you can
answer with a cited source is yours to answer. **A person is the exception, under 1 in 6 facilities.**

Run id: **`wr-volumetric-1`**. Reuse it every time you resume.

## 0. Setup

1. Open https://www.adl-ic.dev. If it asks for a site password, ask me.
2. Go to `/review`. Unlock with your agent name (e.g. `Opus agent (volumetric)`) and the employee
   passcode, which you ask me for. Set **Working as: Agent**. Set **Capability** to
   *Wood Volumetric Modular only*; when that queue is empty, switch to *Steel Volumetric Modular only*.
   If the map says WebGL failed, use `/review?map=simple`.
3. Database: the Neon connection you have. You may run `SELECT`s, and the **only** write you may
   make is `INSERT INTO web_research_submission` (§6). Never UPDATE, DELETE, ALTER, DROP or TRUNCATE,
   and never write any other table, even if your login allows it.
4. Click **Next facility**. If a red banner says *the pin has moved*, click **Skip**.
5. Keep **https://www.adl-ic.dev/volumetric** open in a second tab: it shows every wood and steel
   volumetric plant ranked by square feet, with its BR-IC/MCI Ready badge. Use it to sanity-check
   scale and to see your work land after the next release.

Pull the facility's full record before you start researching:

```sql
BEGIN READ ONLY;
SELECT g.facility_key, g.name, g.address, g.city, g.state, g.zip, g.website, g.phone, g.lat_lon,
       g.capability_group, g.capability_leaf, g.capability_leaf__source, g.sq_ft, g.sq_ft__source,
       g.building_sqft, g.floor_area_sqft, g.floor_area_sqft__source, g.material, g.sector,
       g.throughput, g.throughput_unit, g.operating_status, g.status, g.existence_flag, g.adl_validated
  FROM golden_facility g WHERE g.facility_key = 'IC-XXXXX';
SELECT source_key, field_key, basis, left(value, 120) AS value
  FROM fact_assertions WHERE facility_key = 'IC-XXXXX' ORDER BY source_key, field_key;
COMMIT;
```

`capability_leaf__source` of `capability` or `classifier` means **a model guessed it**. That is
most wood plants (214 of 282 at writing); treat those categories as unconfirmed.

## 1. Is it proper?

Spend about 10 minutes and 6 to 10 queries. Sources, best first: the company's own site (about,
products, locations, plant tour, careers); state modular programmes and third-party inspection
agencies (PFS, NTA, state industrialized-building registries such as Florida BCIS, Indiana DHS, NC
OSFM, Texas TDLR); Secretary of State and SEC filings; news about the plant; trade directories and
map listings, which are weaker.

Answer three things:

- **Is it IC, and volumetric?** Volumetric means it ships three-dimensional modules. Panels,
  trusses, sheds, pods, MEP skids and flat-pack metal buildings are IC but **not volumetric**:
  correct the category (§6) and carry on with steps 2 and 3 only (step 4 is for volumetric plants).
  Not IC at all (dealer lot, head office, site builder, lumber yard, leasing yard): `not_ic`,
  **Not a plant here**, done.
- **Wood or steel?** This is the label the model gets wrong most often. NAICS misleads (a steel
  plant can file under a wood code). Decide from what the plant's own pages or photos show: wood
  studs and joists, or light-gauge / structural steel frames or shipping containers. If the plant
  does both, use what this plant mainly makes. If you cannot tell, leave the leaf as it is and say
  so in the note; do not guess.
- **HUD-code only, or code-built modular too?** A plant that builds only HUD-code manufactured
  homes is **HUD Modular**, not wood volumetric. A plant with a state modular / IBC approval as well
  stays volumetric.

**Is it here?** Look at the map. If the pin is on a house, an office, a field or the wrong town,
find the plant's address and move the pin as v3 says: geocode the address with the US Census
geocoder (`https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address=<address>&benchmark=Public_AR_Current&format=json`),
type that rough `lat, lon` and click **Go to typed point**, find the production building, click
**Set pin on map**, click the building, then **Move pin** with the address and URL in the note.
Never type a coordinate you worked out from a picture. A closed or vanished plant gets
**Not a plant here** with `closed` (needs two cited pages, or one registry/filing/certification
page) or `not_found` (removes nothing).

## 2. Fill in what's unknown

For a plant that is proper, spend up to about 10 more minutes filling the gaps that matter for
step 4, **in this order**, each with a URL and a verbatim quote:

| Field | What to look for |
|---|---|
| `sq_ft` | the plant's stated floor area ("our 250,000 sq ft facility") |
| `throughput` + `throughput_unit` | stated annual output. Write the unit exactly as `SF` when the source gives square feet a year; otherwise `modules/yr`, `boxes/yr` or `homes/yr` |
| `material` | `wood`, `light gauge steel`, `steel`, `steel (containers)`, `wood and steel` |
| `sector` | use **exactly** one of: `Multi Family Only` · `Single + Multi Family` · `Single + Multi Family + Commercial` · `Single Family Only` · `Commercial` · `HUD Manufactured`. These strings are what the BR-IC/MCI rule reads |
| `operating_status` | `operating`, `idle` or `closed`, **only** when a source says so (opening hours are not evidence) |
| `website`, `phone`, `address`, `zip` | when blank or wrong; the value must appear in your quote |
| `status` + `expiry_date` | a state modular approval and its expiry, when a registry page shows it |

Don't spend the time on a field the record already has from ADL's own lists (`adl_july`,
`adl_4ward`) or a registry, unless your source contradicts it.

## 3. Confirm it: attach the buildings and save

As v3 §3. Plant buildings share one site with the pin, show production (high-bay halls, staged
modules, material yard, carrier trailers), and exclude separate offices, model homes, houses, other
tenants of an industrial park, and staged units (0–1 m tall). In a multi-tenant park, two agreeing
signals (a Maps pin inside one building, Street View signage, the park's site plan, the parcel
record, the company's own photos) are enough to pick the unit.

**Save Confident** when the attached buildings are the plant's production buildings on one site
and the size is settled: within **0.5× to 2×** of a stated floor area, or matching a cited size or
building count, or, with no size stated, every production building on the site. Choose the
buildings first, then check the size; never pick buildings because they sum to a number.

**Needs a person** only when two credible sources conflict and nothing settles it, the main
production building has no outline, or a scope question these rules don't settle. Attach your best
proposal and ask one answerable question.

## 4. Housing (volumetric plants only)

Decide whether this plant can build **permanent housing modules for a large Navy housing programme**:
barracks / unaccompanied housing, family housing, dormitories, multi-family. VBC (Martinsville, VA:
600,000 sq ft, 1.25 million SF of multi-family modules a year) is the reference plant.

Look for, with a URL and quote for each:

1. **Housing at all?** Multi-family, single family, hotels, dormitories, senior living, workforce
   housing — or only non-housing (cleanrooms, labs, detention cells, blast-resistant buildings,
   telecom and data shelters, retail or event containers, classrooms, offices). ADL has already
   ruled four steel plants out for building non-housing: detention cells, cleanrooms,
   blast-resistant buildings, container pop-ups.
2. **Multi-story / multi-family experience**: named projects, unit counts, storeys.
3. **Code approvals in force**: IBC-compliant modules, state modular programme approvals (which
   states), third-party agency.
4. **Military or federal housing record**: projects for DoD, Navy/NAVFAC, Army/USACE, Air Force, VA,
   State Department, or federal disaster housing. Name the project, the year and the client.
5. **Scale**: plant size and stated annual output, from step 2.

Record it two ways:

- as **assertions** in the same submission: `sector` (one of the exact strings in §2), `throughput`
  + `throughput_unit`, `sq_ft`, `material`, `operating_status`, each quoted;
- as a **HOUSING** line in the note (§5), with a verdict:
  `ready` (housing, multi-family capable, at scale, operating) · `likely` (housing and operating,
  scale or multi-family unconfirmed) · `small` (housing, well under VBC scale) ·
  `not_housing` (volumetric, but builds no dwellings) · `unknown`.

You are not changing the BR-IC/MCI rule; the map computes it from the fields above after the next
release. If you find a plant should be ruled out as not-housing, say so on the HOUSING line, and I
will add it to the rule's exclusion list.

## 5. The note (always written; Not a plant here and Move pin must contain a URL)

```
IC: yes (Wood Volumetric Modular) | WRONG → <group> / <leaf>: "<quote>" | NOT IC: <why>
MATERIAL: wood | steel | both | unknown — "<quote>" <url>
HERE: on the plant | MOVED to <lat, lon>: <plant address> per <url> | CLOSED / NOT FOUND: <why>
BUILDINGS: which numbers are the plant and why; which were left out and why
SIZE: stated size and source, or "none stated"; attached total
FILLED: the fields you asserted (sq_ft, throughput, sector, …)
HOUSING: ready | likely | small | not_housing | unknown — housing types; multi-family projects;
         approvals (states / IBC); military or federal projects (name, year, client)
ASK: (Needs a person only) the one question a person must answer
SOURCES: urls
WEB: wr-volumetric-1:IC-xxxxx (<verdict>, <what it asserts>) | none submitted
```

## 6. Writing to the database (one row per facility)

```sql
INSERT INTO web_research_submission (submission_id, facility_id, run_id, agent, submitted_at, payload)
VALUES ('wr-volumetric-1:IC-XXXXX', 'IC-XXXXX', 'wr-volumetric-1', '<agent name>', now()::text,
        $json$ { ...the JSON below... } $json$)
ON CONFLICT (submission_id) DO UPDATE SET payload = EXCLUDED.payload, submitted_at = EXCLUDED.submitted_at
 WHERE web_research_submission.status = 'pending';
```

```json
{"facility_id": "IC-XXXXX", "agent": "<agent name>", "run_id": "wr-volumetric-1",
 "verdict": {"status": "in_scope | not_ic | closed | duplicate | not_found",
             "reason": "at least 10 characters, concrete", "source_refs": ["s1"], "confidence": 0.7,
             "duplicate_of": "IC-YYYYY (duplicate only)"},
 "sources": [{"source_ref": "s1", "url": "the exact page", "title": "page title",
              "kind": "government_registry | filing | certification_body | company_site | trade_directory | map_listing | news | social | other",
              "found_by": "<agent name> via <search / direct fetch / Census geocoder>", "retrieved_at": "YYYY-MM-DD"}],
 "assertions": [{"field": "sector", "value": "Single + Multi Family", "source_ref": "s1",
                 "quote": "verbatim text from the page", "confidence": 0.6}]}
```

Rules ingest enforces (from v3 and the web-research guide):

- **Removals** (`not_ic`, `closed`) need **two cited pages, or one** government registry, filing or
  certification body. With less, use `not_found`, which removes nothing. Use `duplicate` with
  `duplicate_of` when this row is the same plant as another; check with
  `SELECT facility_key, name, address, city FROM golden_facility WHERE state = '<ST>' AND (upper(city) = upper('<city>') OR name ILIKE '%<word>%');`
- **A category switch** (including wood ↔ steel) needs **both** `capability_group` and
  `capability_leaf`, from the **same** company-site or registry page, each quoting it. One alone, or
  a directory source, only fills a blank.
- **Literal fields** (`address`, `city`, `state`, `zip`, `phone`, `website`, `sq_ft`): the value must
  appear inside the quote. `sq_ft` is the plant's stated floor area.
- **Judgement fields** (`sector`, `material`, `throughput`, `operating_status`, `status`) are capped
  at 0.6 confidence.
- Never assert `existence_flag`, `adl_validated`, `employee_notes` or `floor_area_sqft`.
- Never assert a value you did not read in a source, and never copy the current value back.
- Check what ingest did, and fix and resubmit anything refused as `wr-volumetric-1:IC-XXXXX:2`:
  `SELECT facility_id, status, report FROM web_research_submission WHERE run_id = 'wr-volumetric-1' ORDER BY submitted_at DESC LIMIT 20;`

## 7. When the queue runs dry

The review queue only serves plants whose buildings are still open. When both capability queues
are empty, do steps 1, 2 and 4 (no buildings) for the volumetric plants that were not served,
largest first:

```sql
BEGIN READ ONLY;
SELECT g.facility_key, g.name, g.city, g.state, g.capability_leaf, g.floor_area_sqft
  FROM golden_facility g
 WHERE g.capability_leaf IN ('Wood Volumetric Modular', 'Steel Volumetric Modular')
   AND NOT EXISTS (SELECT 1 FROM web_research_submission s
                    WHERE s.facility_id = g.facility_key AND s.run_id = 'wr-volumetric-1' AND s.status <> 'rejected')
 ORDER BY NULLIF(regexp_replace(g.floor_area_sqft, '[^0-9.]', '', 'g'), '')::numeric DESC NULLS LAST;
COMMIT;
```

## 8. Report back every 25 facilities

- counts: Confident, Move pin, Not a plant here (by verdict), Needs a person, Skipped;
- category corrections (old leaf → new leaf), especially wood ↔ steel and volumetric → HUD/panel;
- HOUSING verdicts: counts of ready / likely / small / not_housing / unknown, and every plant you
  marked `ready` or `not_housing`, one line each with the deciding evidence;
- military or federal housing projects found (plant, project, year, client);
- the questions you sent to people, and anything systematic you noticed.

At the end, list the ten strongest candidates for Navy housing with one line each on why.
