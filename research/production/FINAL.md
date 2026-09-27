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
