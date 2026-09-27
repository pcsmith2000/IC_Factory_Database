# Web research production: final report (2026-09-27)

Every active golden facility without a web-research submission (4,226) was researched in eight dry batches
(`wr-prod-001` to `008`; 001 and 002 were the same 25). Nothing was written to the warehouse.

| | |
| --- | --- |
| Facilities researched | 4,217 of 4,226 (9 failed on the model's content filter) |
| Facts ready to submit (after today's guards) | 23,422 (zip 2,938, city 2,927, address 2,824, phone 2,697, name 2,282, website 2,094, capabilities and materials 5,386, email 1,260, state 1,014) |
| Verdicts | 1,590 in scope, 356 duplicates, 12 closures (each said in words), 2,259 not confirmed |
| For a person to review | 895 (proposed not_ic, weak or relocated closures, sales) |
| Judge precision on sampled new facts | 0.89 to 1.00 per batch |
| Spend | $10.06 billed of the $18 limit; $32.69 at list price |

Fixes made during the ramp: facts about another company withheld (v9.1/v9.2), a move is not a closure (v9.3),
opt-out mailboxes and ID numbers are not contacts (v9.4), a closure must be said in words and a sale is not a
closure (v9.5). `production.reguard` applies all of them to every batch at submission time.

## Submitting

`research-production-submit.yml` (from main), one dispatch per batch, one at a time:

| run_id | source_run |
| --- | --- |
| wr-prod-002 | 36279000713 |
| wr-prod-003 | 36279827407 |
| wr-prod-004 | 36281219678 |
| wr-prod-005 | 36282766121 |
| wr-prod-006 | 36287672452 |
| wr-prod-007 | 36289267961 |
| wr-prod-008 | 36330192700 |

Then `web-research-ingest` validates and writes the assertions, and the next `golden-refresh` brings them into
golden. Suggested order: 002 first and check ingest's accepted/refused counts before the rest. Artifacts expire
90 days after each run (late December 2026).

## Submitted and ingested (2026-09-27, with the user's approval)

Each batch went through `research-production-submit.yml` (today's guards re-applied) and `web-research-ingest.yml`
(dispatched by hand after each submit; the 15-minute schedule was firing only every few hours).

| Batch | Submissions | Ingested | Partial | Rejected | Facts written |
| --- | --- | --- | --- | --- | --- |
| wr-prod-002 | 25 | 24 | 0 | 1 | 133 |
| wr-prod-003 | 50 | 48 | 0 | 2 | 236 |
| wr-prod-004 | 100 | 98 | 0 | 2 | 487 |
| wr-prod-005 | 200 | 196 | 0 | 4 | 1,081 |
| wr-prod-006 | 400 | 392 | 1 | 7 | 2,333 |
| wr-prod-007 | 799 | 787 | 0 | 12 | 4,651 |
| wr-prod-008 | 2,643 | 2,602 | 2 | 39 | 14,510 |
| **Total** | **4,217** | **4,147** | **3** | **67 (1.6%)** | **23,431** |

Every rejection is the same: a not_found submission with no sources (no page loaded), so nothing true was lost.
The three partials each lost one address whose quote did not state it (ingest's own check). `golden_dirty` was empty
afterwards: golden-refresh has taken the facilities in.
