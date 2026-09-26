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
