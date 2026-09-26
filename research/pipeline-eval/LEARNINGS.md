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
16. **Free website discovery from sibling rows** (prepared, not yet measured). 20 of 61 Dev A-C facilities
    with no website belong to a firm whose other golden rows carry one (Champion, UFP, Canam, Fabcon,
    Schult). Crawling that site first, and skipping the paid search when a page there names this plant's
    city, is free. Matching needs every distinctive name word, excluding place names: first-word matching
    mapped "Phoenix Haus" to phoenix-truss.com and "All American Homes of Colorado" to bldr.com.
17. **First wrong removal** (b-v2c, IC-48445, Boise Cascade Homedale glulam plant). The judge ruled glulam
    "not off-site construction", and one Idaho DEQ air-permit PDF, typed as government_registry because it
    was .gov, met ingest's one-registry-source rule on its own. Fixes agreed with the user: structural wood
    of every kind is named in scope; only listing and licensing .gov pages are registry-grade; a record
    that already carries an IC capability is removed only on two different pages.
18. **Second look works on Dev A** (a-v4): five single-page removals confirmed from other fetched pages
    (Plycraft = furniture, Imerys = kaolin, two Champion sales centers, Brocca), none contradicting an
    in-scope reference. Brocca (garages at another address) should have been not_found.
19. **Dev B generalises** (b-v2c): F2 0.76 (Dev A 0.81), V3 0.75, novel precision 0.95, C1 $0.0089 with
    fresh searches. V1 0.56 is lower than Dev A.
20. **not_ic is the unsafe verdict** (c-v6, S1 = 4 on a fresh batch). Every wrong removal so far is a
    `not_ic` scope or site judgement with enough pages to pass every guard: insulated sandwich panels,
    handcrafted log homes built for shipment, modular steel control buildings, and an ADL anchor whose
    address the company site calls "corporate offices" (the strict-site rule). Guards on evidence count
    cannot fix a judgement about what counts as off-site construction; closures and duplicates have not
    produced a wrong removal.
21. **User decision (2026-09-26): second opinion on not_ic.** A not_ic now stands only when gpt-oss-120b,
    asked independently from the same passages, also answers not_ic; otherwise it is held as not_found with
    both readings in the audit. The scope list names the products c-v6 wrongly removed, and an office
    listing is no longer a reason to remove. Closures and duplicates are unchanged.
22. **The second opinion is not enough on its own** (c-v8). It fixed three of c-v6's four wrong removals,
    but Qwen and gpt-oss-120b agreed that Panelmatic's modular control houses are "electrical control
    panels". Two models sharing one blind spot is not independence.
23. **User decision (2026-09-26): keyword veto on not_ic.** A not_ic is held whenever the passages name an
    in-scope product word. Replayed on past passes, it holds Panelmatic and also some correct removals
    (Champion sales centers mention manufactured homes; Plycraft mentions a panel): removals via not_ic
    become rarer and safe, and V2 still counts them as held.
24. **Provider content filters**: Alibaba refused one facility's pages (HTTP 400 DataInspectionFailed). The
    judge now retries a content refusal once on gpt-oss-120b.
25. **User ruling (2026-09-26): steel-framed greenhouses are out of scope** (Conley, IC-14222). The reference
    had it in scope; the scorer now applies a person's rulings from `adjudications.json` (the ruling only,
    no reference content) and reports which labels it changed. Re-scored, b-v8 has S1 0.
26. **Gate 1 (2026-09-26), user's decision:** run v9 on the holdout now, with V2 "removing >= 50%" waived
    (not_ic removals became human-review by the user's decisions); every other gate judged as written.
    The holdout runs as two capped halves (holdout[0:50], holdout[50:100]) plus the 50 anchors, scored
    together. Preview on Dev B + C combined: every judged gate passes except V1 (0.71 vs 0.85).
27. **V1 is now capped by the safety holds, not the evidence** (v10 = v9 + 12k passages: no change on B or C).
    The in-scope plants v9 misses are held not_ic cases (Panelmatic by the veto; BiltWise, where the second
    opinion answered in_scope) and a same-address duplicate. Next: when the second opinion answers in_scope,
    adopt in_scope rather than not_found (safe: it can only keep a plant).
28. **Gate 1 failed on the verdicts, not the facts or the safety.** Holdout: S1 0 (and 0 on the anchors),
    F1 0.98, F2 0.73, C1 $0.0078 list per facility, 4.8 literal facts per facility against the
    reference's 2.4. V1 0.72 and V2 0.68 fail. The two pull against each other: V2 misses are in_scope calls
    on sites the reference found closed, sold or re-tenanted through historic inventories and news the
    pipeline's one search did not surface; V1 misses are cautious not_found calls (stores, a page marked
    "Closed") and same-address duplicates. See gate1.md.
