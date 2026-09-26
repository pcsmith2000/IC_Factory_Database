# Web research production: the ramp loop

Give this prompt to an agent in this repository and run it with `/loop 10m`. Each wake-up does one small step
and ends. Read this file, `research/pipeline-eval/FINAL.md` and `LEARNINGS.md` (branch
`eval/web-research-pipeline`) whenever you resume.

The pipeline was evaluated on a frozen benchmark (docs/web-research-pipeline-eval.md): on the 100-facility holdout
and the 50 ADL anchors it removed no plant wrongly, agreed with the reference on 98% of shared literal facts,
found 4.8 literal facts per facility (the reference 2.4), and cost $0.0078 list per facility. Its verdicts are
cautious, so production routes every `not_ic` to review and never removes an ADL-validated plant.

## Decisions the user made (2026-09-26)

- **Facts:** every finding that passes the quote and contract checks is submitted, as in the evaluation.
  Directory and news facts fill blanks at low rank; company-site and registry facts can correct.
- **Pace:** the batch doubles after every batch that passes: 25, 50, 100, 200, 400, 800, then 1,000 at a time.
- **Review:** no person between batches. The loop agent reads each batch's audit and the judge model scores a
  random sample of new facts; the gates below decide.

## Hard limits

- **Money:** production has $20. Stop and report at **$18 billed**, counting every batch's pipeline and judge
  cost; record list price too (Tako search is free until 2026-09-30, then $7 per 1,000). A batch's
  `max_cost_usd` is at most `batch_size x $0.012`.
- **Writes:** only the `research-production` workflow's submit job writes, and only pending rows into
  `web_research_submission`; `web-research-ingest` does the rest. The loop agent writes nothing to the
  warehouse by hand. Follow `CLAUDE.md` for reads.
- **Code and configuration:** changes to `main` go through an issue and a pull request. A configuration change
  must first pass the frozen benchmark again (below).
- **Models:** the same price line as the evaluation (ask above $2/M input or $10/M output).

## Every wake-up

1. Read the ledger `research/production/batches.jsonl` (branch `eval/web-research-pipeline`). At $18 billed,
   or when no facility is left to research, write the final report and stop the loop.
2. A batch is running? One line: run id, stage, spend so far. End the wake-up.
3. A batch finished and is not recorded? Read `health.json`, the shards' `audit.md` and the review list, and look
   at the failures themselves. If it was submitted, check ingest (read-only): count the run's submissions by
   status after `web-research-ingest` has run.
   Append the ledger line.
4. Decide the next step:
   - `grow` and ingest refused under 2%: next batch at twice the size, `mode=submit`.
   - `hold`: same size again after a fix, `mode=dry` first; submit only when it grows.
   - `stop`, or two `hold`s in a row at the same size: stop the loop and ask the user.
5. Dispatch it from `main` and end the wake-up. Run id `wr-prod-NNN`, counting up; keep the selection seed.

The first batch (`wr-prod-001`, 25 facilities) runs `mode=dry`, then again as `wr-prod-002` with `mode=submit`
once it grows.

## Gates (in `pipeline/web_research/production.py`)

| Gate | Rule | On failure |
| --- | --- | --- |
| Facilities that errored | <= 5% | hold |
| Quotes not verbatim on the fetched page | 0 | **stop** |
| Findings ingest would refuse | <= 2% | hold |
| List cost per facility | <= $0.012 | hold |
| Closed verdicts | <= 10% of the batch | hold |
| not_ic applied | 0 (always review) | **stop** |
| ADL-validated plant removed | 0 | **stop** |
| Duplicate verdicts | <= 30% of the batch (the backlog holds many real duplicates) | hold |
| Judge precision on 20 new facts | >= 0.85 | hold |
| After ingest: submissions refused | <= 2% of the run | hold |

## Improving between batches

Read the audit's failures. A change (prompt, rule, model, search) is kept only if it:

1. passes the frozen benchmark on Dev A, B and C with cached pages (token cost only): S1 0, F1 and F2 not
   lower by more than 4 points (the noise floor), and
2. then passes the next production batch's gates at the **same** size (`mode=dry`) before the ramp resumes.

Write what you learned to `research/production/LEARNINGS.md`.

## Stop and ask the user if

- a gate says `stop`, or the ramp holds twice at one size;
- ingest refuses more than 5% of a run, or a batch's facts look wrong in a pattern the gates miss;
- spend reaches $18 billed, a pass costs more than 10% over its cap, or the gateway sends a budget alert;
- anything seems to need a warehouse write outside the workflow, more money, or a model above the price line.

## Undoing a batch

Every submission carries its batch's `run_id` (`wr-prod-NNN`) and every assertion it produced points back to it.
To undo one, stop the loop, tell the user, and propose the reversal (new assertions or a survivorship exclusion of
that run) as its own issue: never delete rows from the live warehouse.

## Report

- Per batch, two or three lines: size, decision, facts added, review items, billed and list spend.
- Final: facilities researched, facts added, review list size, spend against the $20, and what to do next.
