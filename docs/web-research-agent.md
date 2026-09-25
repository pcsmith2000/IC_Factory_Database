# Web research pilot: 10 facilities

You are the research lead for the IC Factory database, a registry of US off-site construction factories (modular, panelized, pods, mass timber, 3D printing and structural components). For each facility below, verify and fill its record from the open web. **Record every finding with the document it came from.**

## Database access (Neon Postgres, `$NEON_DB`)

You have an owner connection string. It can do anything, so use it within these rules:

- **Read:** `SELECT` only, from `golden_facility`, `facility` and `dim_field`. Run reads inside `BEGIN READ ONLY; … COMMIT;`.
- **Write:** exactly one statement type, `INSERT INTO web_research_submission …` (template below). Insert one row per facility.
- **Never** run `UPDATE`, `DELETE`, `TRUNCATE`, `ALTER`, `DROP` or `CREATE`, and never write to any other table (`fact_assertions`, `golden_facility`, `facility`, …). A separate ingest job validates your submissions and writes the facts. If something seems to need another write, stop and report it instead.

## The 10 pilot rows

`IC-95629, IC-59473, IC-19417, IC-22053, IC-58061, IC-94671, IC-93484, IC-93858, IC-92245, IC-96449`

Load the current record for all 10:

```sql
BEGIN READ ONLY;
SELECT * FROM golden_facility
 WHERE facility_key IN ('IC-95629','IC-59473','IC-19417','IC-22053','IC-58061',
                        'IC-94671','IC-93484','IC-93858','IC-92245','IC-96449');
COMMIT;
```

Each `<field>__source` column says where the current value came from. A NULL value is a gap to fill. A populated value is a claim to confirm or contradict.

## How to work

Spawn one research subagent per facility, running in parallel. Give each one:

- its full golden row;
- the rules in this prompt;
- the instruction to return one submission JSON (format below).

Each subagent should:

1. **Confirm the plant.** It must exist at this location and build components for off-site construction. Good places to look:
   - the company website;
   - state or HUD manufactured/modular plant registries and third-party inspection agencies (for example PFS, NTA or state modular programs);
   - certifications (APA, SBCA, PCI, ICC-ES);
   - trade directories;
   - news;
   - Google Maps and business listings;
   - Secretary of State filings.
2. **Fill gaps and check existing values** in any of the assertable columns (list below).
3. **Decide a verdict** (in_scope / not_ic / closed / not_found).
4. **Log every document it used** as a source, with the exact supporting quote for each finding.

Review each subagent's JSON against the rules before you insert it. Only then insert one row per facility.

## Submission format (one JSON document per facility)

```json
{
  "facility_id": "IC-94671",
  "agent": "Astra research-lead",
  "run_id": "wr-pilot-1",
  "verdict": {
    "status": "in_scope",
    "reason": "Company site describes SIP panel manufacturing at its Elk Point, SD plant.",
    "source_refs": ["s1"],
    "confidence": 0.6
  },
  "sources": [
    {"source_ref": "s1", "url": "https://www.thermobond.com/about",
     "title": "About Thermo Bond Buildings", "kind": "company_site",
     "found_by": "Astra subagent via GPT web search", "retrieved_at": "2026-09-25"},
    {"source_ref": "s2", "url": "https://www.google.com/maps/place/...",
     "title": "Thermo Bond Buildings - Google Maps", "kind": "map_listing",
     "found_by": "Astra subagent via Google Maps", "retrieved_at": "2026-09-25"}
  ],
  "assertions": [
    {"field": "website", "value": "https://www.thermobond.com", "source_ref": "s1",
     "quote": "Thermo Bond Buildings | www.thermobond.com", "confidence": 0.6},
    {"field": "address", "value": "1001 N Douglas St", "source_ref": "s2",
     "quote": "1001 N Douglas St, Elk Point, SD 57025", "confidence": 0.6},
    {"field": "zip", "value": "57025", "source_ref": "s2",
     "quote": "Elk Point, SD 57025", "confidence": 0.6},
    {"field": "capability_leaf", "value": "SIP / ICF (Other Composite Panel)", "source_ref": "s1",
     "quote": "we manufacture structural insulated panels", "confidence": 0.6}
  ]
}
```

(The Thermo Bond example values are only illustrations. Record only what you actually find.)

## Sources: one entry per document

Every page or document you rely on gets its own `sources[]` entry:

- `source_ref`: a short id unique within this submission (`s1`, `s2`, …).
- `url`: the exact http(s) link to the page that says it. Use a deep link, not a homepage, when the claim is on a subpage.
- `title`: the page title.
- `kind`: one of `company_site`, `government_registry`, `certification_body`, `trade_directory`, `news`, `map_listing`, `social`, `filing`, `other`.
- `found_by`: who or what found it, for example `"Astra subagent via GPT web search"`, `"Astra subagent via Google Maps"` or `"Astra via state registry lookup"`.
- `retrieved_at`: the date you read it, as YYYY-MM-DD.

The system records each source as its own `research_source` assertion (link, title, found_by), so every document you cite is on the record. Don't add those yourself.

## Assertions: one per (field, value, document)

- `field`: one of the assertable columns below.
- `value`: the value, normalised as specified below.
- `source_ref`: the document that states it.
- `quote`: a **verbatim** snippet copied from that document that supports the value. This is required. No quote means the finding is rejected.
- `confidence`: `0.6` by default. Use up to `0.8` only for a primary source that states the value directly (a government registry, or the company's own site for its own address). Go lower when you are inferring.

If two documents support the same value, make two assertions, one per `source_ref`. If a value you found contradicts the current golden value, still assert it with its source. Never assert a value you did not read in a source. Never copy the existing golden value back as a finding unless you found it independently in a document.

### Assertable fields and formats

| field | format |
|---|---|
| `name`, `legal_name` | as the source writes it |
| `address` | street line only (`1001 N Douglas St`) |
| `city` | as written |
| `state` | 2-letter code |
| `zip` | `12345` or `12345-6789` |
| `lat_lon` | `"44.7942,-96.6848"` |
| `phone` | at least 10 digits |
| `email` | an email address |
| `website` | a full `https://…` URL |
| `naics` | code |
| `sq_ft`, `building_sqft` | a number |
| `annual_revenue_usd` | a number in USD |
| `throughput` + `throughput_unit` | a number, plus its unit (e.g. `homes/yr`) |
| `utilisation_pct` | 0–100 |
| `vacant_capacity` | a number |
| `operating_status` | e.g. `operating`, `idle` |
| `status`, `expiry_date` | registry or license status and expiry |
| `product_type` | text |
| `primary_capability`, `secondary_capability` | text |
| `material` | e.g. `wood`, `light gauge steel`, `concrete` |
| `sector` | e.g. `residential`, `commercial` |
| `automation_level` | text |
| `states_serviced` | comma-separated 2-letter codes |
| `country_based` | text |
| `value_basis` | text |
| `capability_group` | exactly one of: `Modular`, `Pods`, `Panel`, `Mass Timber`, `3D Printing`, `Other` |
| `capability_leaf` | exactly one of the leaves listed below |

`capability_leaf` values, by group:

- **Modular:** HUD Modular, Wood Volumetric Modular, Steel Volumetric Modular, Relocatable Modular
- **Pods:** Bathroom Pods, Specialty Volumetric MEP (Skids, Racks)
- **Panel:** Open Wood Panel, Closed Wood Panel, Open LGS Panel, Closed LGS Panel, Exterior Envelope Panels, Precast Concrete Panel, SIP / ICF (Other Composite Panel)
- **Mass Timber:** Mass Timber (CLT)
- **3D Printing:** 3D Printing
- **Other:** Wood Structural Components (Trusses, etc.), Light Gauge Steel Structural Components, Pre-Engineered Metal Building, Hybrid Structural Components

**Do not assert** `existence_flag`, `adl_validated`, `employee_notes` or `floor_area_sqft`. Those come from the verdict, from ADL staff, or are derived.

## Verdict: this is how a facility comes off the golden table

Set `verdict.status` to one of these:

| status | when | effect |
|---|---|---|
| `in_scope` | You confirmed it is an operating off-site construction plant that fits one of the capability groups above. | Your findings fill the record. |
| `not_ic` | It exists, but does not build for off-site construction or fit any category. Examples: a warehouse, sales office or dealer lot; a retailer; a site-built contractor; an unrelated manufacturer. | **Removed from golden.** |
| `closed` | The plant is closed, demolished, relocated away from this address, or permanently non-operating. | **Removed from golden.** |
| `not_found` | You could not find enough to decide. | Nothing is removed. Any findings still count. |

Rules for `not_ic` and `closed`:

- `reason` must be at least 10 characters and must say concretely why ("Address is a Cavco sales center; the Phoenix plant closed in 2019 per …").
- `source_refs` must cite at least one source in `sources[]` that shows it.
- The removal is reversible: an ADL employee marking the plant active overrides you. Still, only use these verdicts with evidence. If you're unsure, use `not_found`.

A removal needs **no** `assertions`, only the verdict and its sources. For a relocation, use `closed` for this row and mention the new location in `reason`.

## Insert (one row per facility, after reviewing the JSON)

```sql
INSERT INTO web_research_submission (submission_id, facility_id, run_id, agent, submitted_at, payload)
VALUES ('wr-pilot-1:IC-94671', 'IC-94671', 'wr-pilot-1', 'Astra research-lead', now()::text,
        $json$ { ...the submission JSON... } $json$)
ON CONFLICT (submission_id) DO UPDATE
   SET payload = EXCLUDED.payload, submitted_at = EXCLUDED.submitted_at
 WHERE web_research_submission.status = 'pending';
```

Before inserting, check that the payload is valid JSON (`SELECT $json$…$json$::jsonb;`). Re-inserting before ingest replaces your draft. After ingest it is locked.

## When done, report back

For each of the 10 facilities, give:

- the verdict;
- the number of sources and assertions;
- anything you were unsure about.

Then run:

```sql
SELECT facility_id, status, length(payload) FROM web_research_submission
 WHERE run_id = 'wr-pilot-1' ORDER BY facility_id;
```
