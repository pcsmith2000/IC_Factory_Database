# Web research: every facility in golden

You lead a research team that verifies and completes every facility in the IC Factory database, a registry of US off-site construction factories (modular, panelized, pods, mass timber, 3D printing and structural components). There are about 6,400 facilities.

Split the work. Spawn subagents to research the facilities. A dedicated **submitter** subagent validates each finished result and inserts it. **Every finding is recorded with the document it came from.**

Run id for this pass: **`wr-full-1`**. Reuse it every time you resume.

## 1. Database access (Neon Postgres)

Use the connection string you were given. If it is the restricted `web_research_agent` login, the database enforces the rules below. If it is an owner login, you must enforce them yourself:

- **Read:** `SELECT` only, from `golden_facility`, `facility`, `dim_field` and `web_research_submission`. Run reads inside `BEGIN READ ONLY; … COMMIT;`.
- **Write:** only `INSERT INTO web_research_submission …` (template in §6). Nothing else, ever.
- **Never** run `UPDATE` (except the draft re-submit in §6), `DELETE`, `TRUNCATE`, `ALTER`, `DROP` or `CREATE`. Never write to `fact_assertions`, `golden_facility`, `facility` or any other table.
- Our ingest job validates submissions hourly and writes the facts. If you think you need any other write, stop and report it.
- **Never dispatch the `web-research-ingest` or `golden-refresh` workflows yourself**, and never ask for them to be run after each batch. Insert your submissions and keep researching; the hourly ingest picks them all up in one run, and the feedback in §7 appears after it.

## 2. The team

**Coordinator (you)** loops until the queue is empty:

1. Fetch the next batch of 25 facilities not yet submitted in this run (query below). The queue is resumable: whatever has been submitted drops out of it.
2. Hand each batch to a **batch lead** subagent. Run up to 8 batch leads in parallel.
3. After every few batches, read the ingest feedback (§7) and pass what was rejected, and why, to the next batch leads. This is how the team improves.

**Batch lead:**

- Spawns one **research subagent** per facility, or researches small batches itself.
- Gives each researcher: the facility's golden row, the rules in §3 to §5, and the instruction to return one submission JSON.
- Collects the JSONs and passes them to the submitter.

**Submitter:** a single subagent, so inserts don't race. It checks every JSON against the checklist in §6 and sends back to the researcher anything that fails. It inserts only what passes, one row per facility.

```sql
-- Next batch: facilities not yet submitted in wr-full-1, the most incomplete records first
BEGIN READ ONLY;
SELECT g.*
  FROM golden_facility g
 WHERE NOT EXISTS (SELECT 1 FROM web_research_submission s
                    WHERE s.facility_id = g.facility_key AND s.run_id = 'wr-full-1'
                      AND s.status <> 'rejected')        -- a refused submission puts the facility back
 ORDER BY (CASE WHEN g.address IS NULL THEN 1 ELSE 0 END + CASE WHEN g.website IS NULL THEN 1 ELSE 0 END
         + CASE WHEN g.phone IS NULL THEN 1 ELSE 0 END + CASE WHEN g.capability_leaf IS NULL THEN 1 ELSE 0 END
         + CASE WHEN g.sq_ft IS NULL THEN 1 ELSE 0 END) DESC, g.facility_key
 LIMIT 25;
COMMIT;
```

Each `<field>__source` column tells you where the current value came from. A NULL is a gap to fill. A populated value is a claim to confirm or contradict.

## 3. How to research one facility

1. **Confirm the plant.** It must exist at this location and build components for off-site construction. Good places to look:
   - the company website: its locations, contact and about pages;
   - state modular and HUD manufactured-housing plant lists and third-party inspection agencies (PFS, NTA, state modular programs);
   - certification bodies (APA, SBCA, PCI, ICC-ES, WTCA);
   - Secretary of State and SEC filings;
   - OSHA establishment records;
   - trade directories;
   - news;
   - Google Maps and business listings.
2. **Check for duplicates.** Look for other rows that may be the same plant: same street address, or the same company in the same city.

   ```sql
   BEGIN READ ONLY;
   SELECT facility_key, name, address, city, website, phone FROM golden_facility
    WHERE state = '<ST>' AND (upper(city) = upper('<city>') OR name ILIKE '%<distinctive word>%');
   COMMIT;
   ```

   If this row and another are the same plant, use the `duplicate` verdict (§5).
3. **Fill gaps and check existing values** in every assertable column (§4). When your source contradicts a current value, assert yours with its source. A registry or the company's own site can correct the database.
4. **Decide a verdict** (§5).
5. **Log every document you used** as a source, with the exact supporting quote for each finding.

Don't research beyond the evidence. If after a reasonable search (about 10 minutes, roughly 6 to 10 queries) you can't confirm the plant, use `not_found` with whatever you did find.

## 4. The submission (one JSON document per facility)

```json
{
  "facility_id": "IC-22053",
  "agent": "Astra batch-lead-3",
  "run_id": "wr-full-1",
  "verdict": {"status": "in_scope",
              "reason": "Company site and Florida DBPR licence place the hollowcore plant at 10980 Hughey Kimal Dr, Venice.",
              "source_refs": ["s1", "s2"], "confidence": 0.8},
  "sources": [
    {"source_ref": "s1", "url": "https://www.myfloridalicense.com/...LicenseDetail?ID=...",
     "title": "Licensee Details - American Precast LLC", "kind": "government_registry",
     "found_by": "Astra research subagent via GPT web search", "retrieved_at": "2026-09-26"},
    {"source_ref": "s2", "url": "https://americanprecastcorp.com/contact",
     "title": "Contact - American Precast", "kind": "company_site",
     "found_by": "Astra research subagent via GPT web search", "retrieved_at": "2026-09-26"}
  ],
  "assertions": [
    {"field": "address", "value": "10980 Hughey Kimal Dr", "source_ref": "s1",
     "quote": "10980 HUGHEY KIMAL DR. VENICE Florida 34292", "confidence": 0.8},
    {"field": "phone", "value": "9414241776", "source_ref": "s2", "quote": "Phone: 941-424-1776", "confidence": 0.7},
    {"field": "website", "value": "https://americanprecastcorp.com", "source_ref": "s2",
     "quote": "americanprecastcorp.com", "confidence": 0.7},
    {"field": "capability_leaf", "value": "Precast Concrete Panel", "source_ref": "s2",
     "quote": "structural precast hollowcore floor, roof, and stair systems", "confidence": 0.6}
  ]
}
```

### Sources: one entry per document

- `source_ref`: a short id unique within this submission (`s1`, `s2`, …).
- `url`: the exact http(s) link to the page that says it. Use the deep link, not the homepage.
- `title`: the page title.
- `kind`: this sets the source's **veracity**, which is the most confidence that source can carry:

  | kind | veracity | can correct existing values? |
  |---|---|---|
  | `government_registry`, `filing`, `certification_body` | 0.8 | yes |
  | `company_site` (the company's own site) | 0.7 | yes |
  | `trade_directory`, `map_listing` | 0.6 | no, only fills blanks |
  | `news` | 0.5 | no |
  | `social`, `other` | 0.4 | no |

  Choose the kind honestly. A directory is not a registry, and a dealer's page is not the company site.
- `found_by`: who or what found it, for example `"Astra research subagent via GPT web search"`, `"… via Google Maps"` or `"… via OSHA search"`.
- `retrieved_at`: the date you read it, as YYYY-MM-DD.

The system records each document you cite as a `research_source` assertion (link, title, found_by), so every source is on the record. Don't add those yourself.

### Assertions: one per (field, value, document)

- `field`, `value` and `source_ref` are required. If two documents support the same value, make two assertions, one per `source_ref`.
- `quote` is required: a **verbatim** snippet copied from that document. For the literal fields below, **the value itself must appear in the quote.** For example, the address quote must contain the house number and street, the zip quote the zip, and the phone quote the digits. A quote that doesn't contain the value is rejected.
- `confidence`: your confidence in this finding, from 0 to 1. The system caps it at the source's veracity. Judgements (the fields marked *judgement* below) are capped at 0.6.
- Never assert a value you didn't read in a source. Never copy the existing golden value back as a finding unless you independently found it in a document.

| field | format | type |
|---|---|---|
| `name`, `legal_name` | as the source writes it | literal |
| `address` | street line only: `10980 Hughey Kimal Dr` | literal |
| `city` | as written | literal |
| `state` | 2-letter code | literal |
| `zip` | `12345` or `12345-6789` | literal |
| `phone` | at least 10 digits | literal |
| `email` | an email address | literal |
| `website` | the site's homepage, `https://example.com`. Any path is stripped. | literal |
| `naics` | code | literal |
| `sq_ft` (plant floor area), `building_sqft` | a number | literal |
| `expiry_date` | YYYY-MM-DD | literal |
| `lat_lon` | `"44.7942,-96.6848"` | literal |
| `status` | licence or registration status | judgement |
| `operating_status` | `operating`, `idle` or `closed`. Only when a source says so; opening hours are not evidence. | judgement |
| `product_type` | text | judgement |
| `primary_capability`, `secondary_capability` | text | judgement |
| `material` | `wood`, `light gauge steel`, `steel`, `concrete`, … | judgement |
| `sector` | `residential`, `commercial`, … | judgement |
| `throughput` + `throughput_unit` | a number, plus its unit (`homes/yr`) | judgement |
| `utilisation_pct` | 0–100 | judgement |
| `vacant_capacity` | a number | judgement |
| `annual_revenue_usd` | a number | judgement |
| `automation_level` | text | judgement |
| `states_serviced` | comma-separated 2-letter codes | judgement |
| `country_based` | text | judgement |
| `value_basis` | text | judgement |
| `capability_group` | exactly one of: `Modular`, `Pods`, `Panel`, `Mass Timber`, `3D Printing`, `Other` | judgement |
| `capability_leaf` | exactly one leaf from the list below | judgement |

`capability_leaf` values, by group:

- **Modular:** HUD Modular, Wood Volumetric Modular, Steel Volumetric Modular, Relocatable Modular
- **Pods:** Bathroom Pods, Specialty Volumetric MEP (Skids, Racks)
- **Panel:** Open Wood Panel, Closed Wood Panel, Open LGS Panel, Closed LGS Panel, Exterior Envelope Panels, Precast Concrete Panel, SIP / ICF (Other Composite Panel)
- **Mass Timber:** Mass Timber (CLT)
- **3D Printing:** 3D Printing
- **Other:** Wood Structural Components (Trusses, etc.), Light Gauge Steel Structural Components, Pre-Engineered Metal Building, Hybrid Structural Components

Base capability judgements on what **this plant** makes, from a page describing it. A third party's list of collaborators is not evidence.

**Do not assert** `existence_flag`, `adl_validated`, `employee_notes` or `floor_area_sqft`. They come from the verdict, from ADL staff, or are derived.

## 5. Verdict: this is how a facility comes off the golden table

| status | when | effect |
|---|---|---|
| `in_scope` | You confirmed it is an operating off-site construction plant that fits one of the capability groups. | Your findings fill and correct the record. |
| `not_ic` | It exists, but doesn't build for off-site construction. Examples: a head office, sales centre, dealer lot, warehouse or retailer; a site-built contractor; an unrelated manufacturer. | **Removed from golden.** |
| `closed` | The plant is closed, demolished, moved away from this address, or permanently non-operating. | **Removed from golden.** |
| `duplicate` | This row is the same plant as another row. Set `"duplicate_of": "IC-#####"` to the row that should survive, normally the more complete one. | Queued for merge review. Your findings still count. |
| `not_found` | You couldn't find enough to decide. | Nothing is removed. Your findings still count. |

Rules:

- `not_ic`, `closed` and `duplicate` each need a concrete `reason` of at least 10 characters (for example "3636 N Central Ave is Cavco's head office per its 10-K; the Phoenix plant is 2502 W Durango St") and `source_refs` citing at least one source that shows it.
- These verdicts are reversible: an ADL employee marking the plant active overrides you. Still, use them only with evidence. If you're unsure, use `not_found`.
- For a relocation, use `closed` on this row and mention the new address in `reason`. Do **not** assert the new address on this row.
- **A removal (`not_ic` or `closed`) needs two cited sources on different pages, or one `government_registry`, `filing` or `certification_body` source.** A single directory, map listing, news item or social page is not enough; the verdict is refused and the facility stays.
- **Every submission needs at least one source.** For `not_found`, cite the pages you checked; they are recorded as the search. A submission with no sources is refused and the facility goes back into the queue.
- A removal needs no assertions: the verdict and its sources are enough.

## 6. Submitter: checklist and insert

Check each JSON before inserting it:

- `facility_id` is the row's id, and `run_id` is `wr-full-1`.
- Every `assertions[].source_ref` exists in `sources[]`, and every source has a url, kind, found_by and retrieved_at.
- Every literal value appears in its quote. Websites are homepages. States are 2-letter codes.
- `capability_group` and `capability_leaf` use the exact names in §4.
- A `not_ic`, `closed` or `duplicate` verdict has a reason and `source_refs`.
- The payload is valid JSON: `SELECT $json$…$json$::jsonb;`.

```sql
INSERT INTO web_research_submission (submission_id, facility_id, run_id, agent, submitted_at, payload)
VALUES ('wr-full-1:IC-22053', 'IC-22053', 'wr-full-1', 'Astra submitter', now()::text,
        $json$ { ...the submission JSON... } $json$)
ON CONFLICT (submission_id) DO UPDATE
   SET payload = EXCLUDED.payload, submitted_at = EXCLUDED.submitted_at
 WHERE web_research_submission.status = 'pending';
```

Re-inserting before ingest replaces the draft. After ingest it is locked. To correct an ingested row, or to redo a refused one, submit a new row with `submission_id = 'wr-full-1:IC-22053:2'`.

## 7. Feedback loop (coordinator, after every few batches)

```sql
BEGIN READ ONLY;
SELECT status, count(*) FROM web_research_submission WHERE run_id = 'wr-full-1' GROUP BY 1;
SELECT facility_id, status, report FROM web_research_submission
 WHERE run_id = 'wr-full-1' AND status IN ('partial', 'rejected') ORDER BY processed_at DESC LIMIT 50;
COMMIT;
```

`report.rejected` lists every refused finding and the reason. Feed the common reasons back to the batch leads. Resubmit a corrected document for any facility whose findings were refused.

## 8. Report back

Give a short summary at each checkpoint (every 500 facilities) and at the end:

- submissions by verdict;
- ingest status counts;
- the top rejection reasons;
- anything systematic you noticed, such as a source that's often wrong or a cluster of duplicates.
