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
4. **Fix (#72, PR #73): production-v9.1.** A record sent to review, or downgraded for another street, keeps no
   facts; a found address with neither the record's house number nor its street withholds the contact facts.
   Offline replay of v9 Dev A–C: S1 0, F1 1.00 and F2 0.78 unchanged, 5.00 → 4.65 facts per facility (the six
   facilities losing facts are all reference not_found, not_ic or duplicate). wr-prod-001: 35 facts withheld
   from the 6 review records, CMH's included. Duplicate gate raised to 30%. Next: wr-prod-002, 25, dry.
5. Replaying a guard on finished passes (trace.json + submissions.jsonl) costs nothing and is exact when the
   guard only drops facts; use it before spending on Dev A–C.
