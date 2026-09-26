# Web research pipeline evaluation: learnings

Running notes from the tuning loop (design: `docs/web-research-pipeline-eval.md`; ledger: `passes.jsonl`;
configs: `configs/`). Each pass also uploads `audit.md` (per facility: search, pages, verdict and reason,
every finding with its source and quote, what was dropped and why, cost) and `scorecard.md`.

## Process

- **Every pass states a hypothesis** in its config (`hypothesis`), and the audit and job summary print it.
- **Passes run in parallel.** Each run is its own concurrency group and its own cap; the ledger sums them.
  Extraction and model passes re-use the cache and cost only tokens, so bake-offs run side by side.
  Caveat: parallel passes each save their own cache key and the next pass restores only the newest one,
  so run search-changing passes one at a time.
- **Open-weight models first.** The judge candidates are open-weight where they can do the job
  (gpt-oss-20b/120b, Qwen 3.7 Flash, DeepSeek V4 Flash); a closed model has to beat them to be chosen.
  Search still needs a model that reliably calls the gateway tool (see below).
- **Adjudication cost counts** toward the ledger's cumulative spend, not only the pipeline's.
- **Read the audit, not just the metrics.** Every change so far came from reading failures.

## Findings

1. **A search must be confirmed** (smoke-1). `qwen3.7-flash` answered the search call without invoking
   `vercel:tako_search` and invented `example.com`. The pipeline now counts a search only when the gateway
   reports `gatewayToolCalls`, retries once on a fallback model, and never caches an unconfirmed search.
   `gemini-3.1-flash-lite` calls the tool reliably (5/5, then 16/16).
2. **Ask the search model for URLs only** (smoke-2). With snippets, every reply hit `max_tokens`, the JSON
   was cut off, and no URLs parsed. URL+title only, a tolerant parser, and caching the raw reply
   (re-parsed on read) fixed it. The search call's output tokens were the largest token cost.
3. **Hidden reasoning eats the output budget** (smoke-3). DeepSeek V4 Flash spent all 1,500 output
   tokens reasoning on one facility; the adjudication judge's 200-token cap left every answer empty
   ($0.016 wasted). Reasoning effort is now `low` with headroom.
4. **Baseline v0 on Dev A** (run 36258860691): S1 0, primary 45.8 (F2 0.46 × F1 1.0), V1 0.50,
   V2 1.0 but removing 0%, V3 0, C1 $0.0074 list ($0.0108 per searched facility: the $0.007 Tako search
   is two thirds of it). The pipeline asserts 3.2 literal facts per facility against the reference's 1.9,
   and its novel findings are 85% correct.
   - Most F2 misses are city/state/website the reference re-asserts although they match the record.
   - Wrong novel findings are mostly regex emails from directories and newspapers, and values from a
     different plant in the same town.
   - Removals are found (Eklof = docks, Amcor = closed) but on one page, so policy correctly holds them
     as `not_found`: removal needs a second page or a registry.
   - The reference found several plants in registry PDFs (MHI plant list, Missouri PSC); one Tako search
     did not surface them.
   - The pipeline flagged two duplicates by same phone / same address that the reference kept in scope.
5. **Extraction options plus an open-weight judge lift F2 sharply** (a-v1-*, cached evidence, tokens only).
   gpt-oss-120b: primary 79.2 (F2 0.79, V1 0.875, V3 0.5, 5.7 literal facts per facility, novel precision
   0.92) at $0.0008 per facility in tokens. gpt-oss-20b: primary 62.5 at $0.0002 but 11.6% contract
   refusals. Open-weight models are competitive here; the judge is not the cost driver, search is.
6. **Company versus site** (a-v1-gptoss120b V2 0.29). A capable judge credits the company's products to
   this address: a plant that moved (Phoenix Haus to Colorado), a site now another business (Canam,
   Lafayette), a retail center (Champion, McMinnville), a different plant of the same firm (Jensen), or
   only historical records (All American Homes, 2014). These are in_scope errors, not removals (S1 stays
   0), but they are the opposite failure and V2 counts them. Tested next: a strict-site rule.
7. **Refusals are mechanical** (S2): scheme-less websites, state names, off-taxonomy leaves and quotes
   that do not contain the value. Running the contract's own checks before submission repairs the first
   two and drops the rest, at no cost.
8. **Qwen 3.7 Flash is the best judge so far** (a-v1-qwen37): primary 81.25, V2 0.71, V3 0.5, novel
   precision 0.95, $0.0004 per facility, open weight. gpt-oss-120b with prevalidate and strict-site
   (a-v2a) reaches primary 83.3 with S2 0 and V1 1.0, but V2 only 0.43.
9. **Prevalidation takes S2 to 0** (a-v2a, a-v2b), with no loss of coverage.
10. **Prompt rules do not fix the company-versus-site error.** The evidence is in the pages (Phoenix Haus's
    relocation to Grand Junction; Jensen's plant at a different address) and the judge still says
    in_scope. A two-source removal instruction did not add removals and cost V1 and F2 (reverted).
    Next: deterministic guards (an in_scope whose evidence names another street address is held as
    not_found) rather than more prompt text.
11. **The reference misses duplicates the pipeline finds** (a-v2c). IC-58495 and IC-95450 are identical
    golden rows (FABCON Grandville, same address and phone); the reference kept IC-58495 in scope.
    Scoring a disagreement against the reference as an error is wrong here, so the scorer adds
    V1_adjudicated: a pipeline duplicate whose target shares the record's phone or street address
    counts as correct.
12. **Two-source instruction: dropped** (hurt F2 and precision on both gpt-oss-120b and Qwen).
13. **Speed**: gpt-oss ~3 s per facility, Qwen ~16 s, DeepSeek V4 Flash > 45 s (a-v1-deepseek still
    running after 20 minutes on cached pages). Runner time is free, but a 4,500-facility run at DeepSeek's
    pace would need ~56 hours of jobs.
14. **Noise floor**: identical verdicts, different primary (79.4 vs 77.1) between two runs of the same
    judge at temperature 0 (a-v2c, a-v3). On 25 facilities, primary differences under ~4 points are noise;
    confirm on a second batch before keeping a change for its primary alone.
15. **Removals are found, then held.** Qwen proposes the reference's removals (Eklof = docks, Amcor =
    closed, Champion McMinnville = retail) but each on one page, and the ingest rule (two pages or a
    registry) holds them as not_found. Next: a token-only second look over the other fetched pages.
