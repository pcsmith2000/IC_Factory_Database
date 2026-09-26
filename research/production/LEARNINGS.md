# Web research production: learnings

Ramp loop: docs/web-research-production-loop.md (main). One line per batch in `batches.jsonl`.

Starting point: configuration production-v9 (`pipeline/research_eval/configs/production.json`), evaluation
cumulative $3.46 list. Production budget $20, stop at $18 billed.

## wr-prod-001 (25, dry): hold, loop stopped

1. **Duplicates are commoner in the unresearched population.** 5 of 25 (20%) against a 15% gate, and all five are real
   (same street address or phone as an active record). The gate measures the backlog, not a pipeline fault.
2. **Facts about the wrong company slip past the verdict guards.** IC-95539 (CMH Manufacturing #927, Clayton Homes'
   White Pine TN plant) was matched to cmhmfg.com, a Lubbock TX foundry-equipment maker. The judge said not_ic;
   production sent it to review, but the Lubbock address, city, ZIP and phone were still submitted. The address
   guard only changes verdicts; it never drops the facts. The judge's sample caught the ZIP as incorrect.
3. Cost is on plan: $0.0080 list per facility for the pipeline, $0.0009 for the judge's sample.
