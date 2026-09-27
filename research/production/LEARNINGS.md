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

## wr-prod-002 (25, dry, v9.1, same facilities as 001): grow

6. The fix held: the 6 review records submitted no facts; judge precision 0.94 (the one miss: Nucor's website
   given as metlspan.com, a subsidiary brand). Facts per facility fell 5.6 → 3.9, mostly the withheld review records.
7. **Verdicts are not stable at the margin.** 6 of 25 changed between two identical runs (in_scope ↔ duplicate or
   not_found). The checked duplicates are defensible (same address or phone), but a verdict seen once is weak
   evidence. Worth measuring on the next batches before any fix: agreement between two runs costs a batch.

## wr-prod-003 (50, dry, offset 25): grow

8. Judge precision 1.0 on 19 facts; duplicates 14%. Cost steady at $0.0080 list per facility.
9. **Review is getting crowded with real plants.** 13 of 50 went to review (proposed not_ic). Several are in scope:
   Tower Structural Laminating (glulam; judged a Wabash trailer dealer), Twin Oaks Truss, Phoenix Modular Elevator,
   Coach House (garages). Safe (no removal), but withholding their facts costs comprehensiveness. Watch the share at
   100; if it stays above ~20%, test a fix on Dev A–C (e.g. keep literal facts when the page's address matches the
   record's, and only withhold when it does not).

## wr-prod-004 (100, dry, offset 75): grow

10. Two shards of 50 in parallel: 100 facilities in 27 min. List cost fell to $0.0069 per facility.
11. First `closed`: Fitts Company's Lexington SC plant, relocated to Gaston SC in 2016 (company site and SC Commerce).
    Correct, but a **relocation is also a new plant**: the Gaston site should be in the table. The pipeline has no
    way to propose one. Record relocations for a follow-up (comprehensiveness), don't only close the old site.
12. Review share 19% (19/100), mostly genuinely out of scope (school districts' "plant operations", Eli Lilly,
    cargo trailers, a Swedish head office). The v9.2 own-address rule applies from wr-prod-005.

## wr-prod-005 (200, dry, offset 175, v9.2): gates grow, loop STOPPED on a wrong removal

13. **A move is not a closure.** Power Truss (IC-55060) was judged `closed` from its own contact page: "(Former
    Address: 935 W. Housman)". The same page shows the company operating in Mayfield (1009 KY-121) with the record's
    phone. The removal rule (two pages, or one registry) was met by two pages of the company's own site, both saying
    the plant is alive. The gates count closed verdicts but never check them; the loop's hand check caught it.
    Fitts (wr-prod-004) was the same pattern across towns. Fix before resuming: a `closed` whose evidence speaks of a
    former address, a move or a relocation, or whose company site is live with the record's phone, is not a removal.
14. Otherwise the batch was clean: 200 in ~28 min (4 shards), judge precision 1.0, $0.0075 list per facility.
