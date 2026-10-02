# Loop prompt: tune the low-cost web research pipeline

Give this prompt to an agent working in this repository and run it with `/loop 10m`. Each wake-up does one small
step and ends. The design it follows is `docs/web-research-pipeline-eval.md`; read that first, every time you resume.

---

You are building and tuning a low-cost web research pipeline for the IC Factory database. The goal is a pipeline, run
as GitHub Actions, that matches or beats the research agent's `wr-full-1` pass on a frozen benchmark, at a cost per
facility that lets the remaining production budget cover as much of the table as possible. You work in small,
measured passes. You write **nothing** to the warehouse. When the holdout gate passes, you stop and report; the user
decides about production.

**You have wide latitude in how you get there.** The design's pipeline (§3) is a starting point, not a specification to
defend. Choose which models to try, how to split work between free steps, cheap models and paid search, how to find a
plant's pages, how to write the prompts, what to measure beyond the required metrics, and what to test next. Think
like a researcher with a small grant: spend where you learn the most per dollar, and write down why.

## Hard limits (never cross these; ask the user instead)

- **Money.** The evaluation has **$10**; stop and report at **$8** cumulative, counting search at list price even
  while it is free. Each pass sets `max_cost_usd`, never more than $1.00 (start with $0.10 for the smoke test). Use the
  repository secret `AI_GATEWAY_API_KEY`: it routes to the budgeted key for this work. The key is shared, so before a
  pass check that no other workflow using it (`tako-*`, `capability`, `bakeoff`, `enrich`) is running. Never create a
  key or change a budget.
- **Models.** Any gateway model is allowed, as long as the configuration's average cost stays inside the cost gate
  (C1). Ask first before using a model priced above $2 per million input or $10 per million output tokens. Free,
  stealth and preview models may be explored but cannot be the recommended configuration. Open-weight models served
  on the runner are allowed. Deep or multi-search modes need the user's approval. Re-read the gateway catalog at the
  start of each pass and record the prices you used.
- **Warehouse.** Read-only. Never insert into `web_research_submission` or write any table during the evaluation.
- **Benchmark data.** Never commit the benchmark, reference payloads or holdout outputs to git (the repository is
  public). Never place a reference answer in a model prompt. Open the holdout only at a gate, and read only its
  scorecard and listed failures.
- **Code.** Every code change to `main` goes through a GitHub issue and a pull request; another agent merges. Tuning
  changes live on the evaluation branch `eval/web-research-pipeline` and are dispatched from it. Follow `CLAUDE.md`
  for warehouse access.

## Stop at once and ask the user if

- any pass produces a wrong removal (S1 > 0): show the facility, both verdicts and both sources;
- a pass's cost exceeds its `max_cost_usd` by more than 10% (an accounting bug), or the gateway sends a budget alert;
- the ingest contract (`pipeline/web_research/ingest.py`, `docs/web-research-agent.md`) changes on `main`;
- anything seems to need a warehouse write, more money, or a model above the price line.

## Phase 0: build the harness (once)

Design and build what the evaluation needs, in one issue and one pull request, and wait for it to merge before any
paid pass. At minimum:

- **Benchmark export**, read-only in Actions, exactly as design §2 describes. The input is rebuilt by survivorship
  without source `web_research`; the labels are the reference verdict, strong or weak removal, the duplicate target
  and the accepted findings. Include the ADL anchor set and a seeded split. The output is an artifact with its SHA-256.
- **The pipeline**, configurable by one JSON file so a pass can change one thing. Two things are not optional: every
  quote must occur verbatim in a page the pipeline fetched, and every submission must pass
  `pipeline.web_research.ingest.plan()` offline. Search results are cached for the whole evaluation.
- **A scorer** for the §4 metrics with the adjudication steps, writing `scorecard.json` and the job summary.
- **A dispatch-only workflow** with its own concurrency group, reading the warehouse read-only and uploading every
  fetched page, model response, submission, cost record and scorecard as the run artifact.
- **Tests** for the quote check, the cost ceiling, the normalised comparisons, the split being deterministic, and that
  no code path writes to the warehouse.

Then export the benchmark, record its run id and hash in the ledger, and run a **smoke test** on 5 facilities to prove
the plumbing end to end.

## Every wake-up

1. **Read the ledger** `research/pipeline-eval/passes.jsonl` on the evaluation branch. At $8 or more cumulative, write
   the final report and stop the loop.
2. **A pass is running?** Report one line (pass id, progress, spend so far) and end the wake-up.
3. **A pass finished and is not scored?** Read its scorecard, check its cost against the cap, and look at the failures
   themselves, not only the numbers. Append the ledger line with what you learned, and keep or revert the change.
4. **Design the next pass.** Pick the experiment most likely to move the primary score, or the cost, per dollar spent.
   Run the holdout gate when design §6 says it is time.
5. **Dispatch** it and end the wake-up.

## Designing passes

Use your judgement. A few principles:

- **Search is most of the cost.** A search costs $0.005 to $0.007; a call to the cheapest capable models costs a few
  hundredths of a cent. Levers that avoid searches (crawling the known website, free website discovery, better use
  of the pages already fetched) usually beat model changes.
- **Re-use evidence.** A pass that changes only extraction or judgement should re-use cached pages and search results
  and cost only tokens. That makes model and prompt experiments almost free, so run them first.
- **Compare like with like.** Measure a candidate against the best configuration on the same batch, and confirm on a
  fresh batch before you trust it.
- **Read the failures.** A metric tells you that something is wrong; ten failed cases tell you what to try next.
- **Start cheap, escalate on evidence.** Begin with the cheapest capable models: DeepSeek V4 Flash (0731), which has
  the lowest output price with a 1M context, gpt-oss-20b, Qwen 3.7 Flash and GPT-5 nano are good first candidates.
  Move a step up only where the failures show the model is the cause, and consider routing only the hard cases
  there.

Ideas worth testing, in no required order: a model bake-off on cached evidence; the passage budget; regular
expressions against the model for literal fields; crawl depth and link selection; free website discovery (Overture
places near the facility's coordinate, registry records the repository already loads); search provider (Tako,
Perplexity, Parallel) and query wording; the removal and duplicate policy; an open-weight model on the runner;
self-consistency or a second opinion from a cheap model on removals only. Add your own.

## Ledger and reporting

- **Ledger line per pass:** pass id, commit, benchmark hash, what changed and why, batch, facilities, billed $, list $,
  cumulative $, the §4 metrics, kept or reverted, and one sentence on what you learned.
- **Each pass**, report two or three lines: the change, kept or reverted, the metric that moved, the pass cost and the
  cumulative spend.
- **At each gate**, report a table of every §4 metric for the pipeline beside the reference, the ten adjudicated cases
  for the user to check, and the go / no-go.
- **Final report:** the winning configuration and why, the holdout scorecard, spend against the $10, the projected
  list-price cost for the roughly 4,500 facilities `wr-full-1` has not researched, and the batch size you recommend for
  the first production run. Then stop the loop. Production is the user's decision.
