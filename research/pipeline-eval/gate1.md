# Gate 1 (2026-09-26): v9-qwen-veto on the holdout

Holdout of 100 facilities (two capped halves, scored together) and the 50-facility ADL anchor set, benchmark
`ef59450b`. V2 "removing" waived by the user; every other gate judged as designed. Metrics only: no reference
content is committed here (the repository is public).

| Metric | Holdout | Gate | Result |
| --- | --- | --- | --- |
| S1 wrong removals (holdout + anchors) | 0 + 0 | 0 | pass |
| S2 findings ingest would refuse | 0.0% | <= 5% | pass |
| S3 quotes not verbatim | 0 | 0 | pass |
| V1 in-scope agreement | 0.72 (0.78 adjudicated) | >= 0.85 | **fail** |
| V2 strong removals removed or held | 0.68 | >= 0.80 | **fail** |
| V2 removing | 0.05 | >= 0.50 | waived |
| V3 duplicates with the same survivor | 0.53 | reported (target 0.50) | - |
| F1 literal agreement (adjudicated) | 0.98 | >= 0.90 | pass |
| F2 coverage of reference literals | 0.73 | >= 0.70 | pass |
| F3 novel literal findings, precision | 300, 0.87 | reported (0.90 to count) | - |
| J1 capability/material agreement | 0.93 | reported | - |
| C1 list cost per facility | $0.0078 (billed $0.0023) | <= $0.010 | pass |
| C2 list cost per accepted literal fact | $0.0016 | reported | - |
| T1 runner minutes per facility | 0.43 | reported | - |
| Literal facts per facility | 4.79 (reference 2.44) | - | - |

Anchors (50): S1 0; 31 in_scope, 11 not_found, 8 duplicate (merge candidates for review, not removals).

**Go / no-go: no-go.** The verdict gates fail; per design section 6 the evaluation returns to tuning once.
