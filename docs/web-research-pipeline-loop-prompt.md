# Loop prompt: tune the low-cost web research pipeline

Give this prompt to an agent working in this repository and run it with `/loop 10m`. Each wake-up does one small
step and ends. The design it follows is `docs/web-research-pipeline-eval.md`; read that first, every time you resume.

---

You are tuning a low-cost web research pipeline for the IC Factory database until it matches or beats the research
agent's `wr-full-1` pass on a frozen benchmark, spending as little as possible. You work in small passes of about 25
facilities. You write **nothing** to the warehouse. When the holdout gate passes, you stop and report, and the user
decides about production.

## Hard limits (never cross these; ask the user instead)

- **Money.** The evaluation has **$10**; stop and report at **$8** cumulative (billed or list price, whichever is
  higher). Each pass sets `max_cost_usd`: $0.10 for the smoke test, $0.50 by default, never more than $1.00. Use only
  the `AI_GATEWAY_EVAL_KEY` secret, never `AI_GATEWAY_API_KEY`. Never raise a cap, create a key, or change a budget.
- **Models and search.** Only the Tier 0 and Tier 1 models and the search tools listed in the design (§5). Tier 2
  models only for adjudication, within its $0.25 per pass. No free, stealth or preview models. No deep or multi-search
  modes. Re-read the gateway catalog at the start of each pass and record the prices used.
- **Warehouse.** Read-only. Never insert into `web_research_submission` or write any table. The pipeline's
  `submit` step stays disabled for the whole evaluation.
- **Benchmark data.** Never commit the benchmark, reference payloads or holdout outputs to git (the repository is
  public). Never place a reference answer in a model prompt. Open the holdout only at a gate, and read only its
  scorecard and listed failures.
- **Code.** Every code change goes through a GitHub issue and a pull request to `main`, as in this repository's other
  work; another agent merges. Tuning changes live on the evaluation branch `eval/web-research-pipeline` and are
  dispatched from it. Follow `CLAUDE.md` for warehouse access.

## Stop at once and ask the user if

- any pass produces a wrong removal (S1 > 0): show the facility, both verdicts and both sources;
- a pass's cost exceeds its `max_cost_usd` by more than 10% (an accounting bug), or the gateway sends a budget alert;
- the ingest contract (`pipeline/web_research/ingest.py`, `docs/web-research-agent.md`) changes on `main`;
- anything seems to need a warehouse write, a larger budget or a model outside the ladder.

## Phase 0: build the harness (once)

Open one issue and one pull request that add the following, then wait for it to merge before any paid pass:

1. `pipeline/web_research/benchmark.py` with an `export` mode, a read-only transaction in Actions that builds the
   benchmark exactly as design §2 describes: the pre-research input rebuilt by survivorship without source
   `web_research`, reference labels (verdict, strong or weak removal, duplicate target, accepted findings), the ADL
   anchor set, and the stratified split with a fixed seed. Output is an artifact with its SHA-256.
2. `pipeline/web_research/pipeline.py`, the pipeline of design §3: crawl, conditional search with a per-evaluation
   search cache, deterministic extraction, one model call on selected passages, verbatim quote check, and an offline
   `ingest.plan()` contract check. The configuration (model, search provider, crawl depth, passage budget, prompts)
   is one JSON file so a pass changes exactly one thing.
3. `pipeline/web_research/score.py`: the §4 metrics, deterministic adjudication, the capped judge, and the scorecard
   written to the job summary and as `scorecard.json`.
4. `.github/workflows/web-research-pipeline.yml`, dispatch only, with modes `benchmark_export`, `eval` and `gate`.
   Inputs: `benchmark_run`, `benchmark_sha256`, `batch` (`devA`, `devB` … `devF`, `holdout`, `anchor`, `smoke`),
   `config` (a path), `max_cost_usd`. It uses its own concurrency group, reads the warehouse read-only, and uploads
   every fetched page, model response, submission, cost record and scorecard as the run artifact.
5. Tests: the quote check, cost ceiling, normalised comparison, the split being deterministic, and that no code path
   writes to the warehouse.

Then run `benchmark_export`, record its run id and hash in the ledger, and run the **smoke test**: 5 facilities,
$0.10, to prove the plumbing end to end.

## Every wake-up

1. **Read the ledger** `research/pipeline-eval/passes.jsonl` on the evaluation branch. At $8 or more cumulative, write
   the final report and stop the loop.
2. **A pass is running?** Report one line (pass id, progress, spend so far) and end the wake-up.
3. **A pass finished and is not scored?** Read its `scorecard.json`, check its cost against the cap, append its ledger
   line, and decide: **keep** the change if the primary score (F2 × F1) improved and no safety metric worsened,
   otherwise **revert** it.
4. **Choose the next pass**: the gate if its condition in design §6 is met; a fresh Dev batch if this is the third pass
   since the last one; otherwise the top untested hypothesis below, one change only.
5. **Dispatch** it on `eval/web-research-pipeline` with the benchmark run and hash, and end the wake-up.

### Hypotheses, in order (cheapest and most informative first)

1. **Model bake-off on cached evidence.** Same pages, four Tier 0 models. Search is re-used, so this costs tokens only.
2. **Passage budget.** About 3,000 against 6,000 input tokens per facility.
3. **Regular expressions against the model** for phone, ZIP, email and address.
4. **Crawl depth.** Four against eight pages; which link words find the plant address.
5. **Free website discovery** before paid search: Overture places near the facility's coordinate.
6. **Search provider**, on facilities that need search: Tako fast, Perplexity, Parallel.
7. **Search query wording** (name, city and state, plus "manufacturing plant").
8. **Removal and duplicate policy.** Prompt wording for `not_ic`, `closed` and `duplicate`, judged on V2, V3 and S1.
9. **Tier 1 for one stage**, only where a pass showed Tier 0's errors are the model's fault, and only if C1 still passes.

Add a hypothesis when a pass's failures suggest one. Record why in the ledger line.

## Reporting

- **Each pass**, two or three lines: the change, kept or reverted, the metric that moved, the pass cost and the
  cumulative spend.
- **At each gate**, a table of every §4 metric for the pipeline beside the reference, the ten adjudicated cases for the
  user to check, and the go / no-go.
- **Final report**: the winning configuration, the holdout scorecard, spend against the $10, the projected list-price
  cost for the roughly 4,500 facilities `wr-full-1` has not researched, and the batch size you recommend for the first
  production run. Then stop the loop. Production is the user's decision.
