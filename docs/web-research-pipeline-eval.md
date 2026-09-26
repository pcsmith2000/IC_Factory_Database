# Web research pipeline: evaluation design

**Question.** Can a low-cost research pipeline, run as GitHub Actions like our other pipelines, match or beat the
research agent's `wr-full-1` pass on the same facilities, at a cost we can afford for the rest of the table?

**Answer format.** A scorecard on a frozen benchmark, produced in small passes that together spend at most **$10**,
write **nothing** to the warehouse, and end in a go / no-go for production runs funded from the rest of the
$30 allocation.

The companion prompt for the agent that runs the passes is `docs/web-research-pipeline-loop-prompt.md`.

## 1. Budget

| Phase | Cap | What it pays for |
| --- | --- | --- |
| Evaluation | **$10 hard**, stop and report at $8 | Smoke test, tuning passes, adjudication, two holdout gates |
| Production | $20 | Only after the holdout gate passes and the user says go |

Three independent controls, so no single bug can overspend:

1. **A dedicated gateway key with its own limit.** The user creates it and stores it as the repository secret
   `AI_GATEWAY_EVAL_KEY`; the eval never uses the shared `AI_GATEWAY_API_KEY`.

   ```bash
   vercel ai-gateway api-keys create --name ic-research-eval --limit 10 \
     --refresh-period monthly --alert-thresholds 50,80,100 --expiration 30d
   ```

2. **A per-pass ceiling.** Every pass declares `max_cost_usd` (default $0.50, smoke test $0.10). The pipeline stops
   starting new facilities when the spend so far plus the worst case for one more facility would cross it.
3. **A ledger.** Every pass appends its billed cost and its list-price cost to the pass ledger (§6). The loop stops at
   $8 cumulative, whatever the gateway says.

**Billed and list price are both recorded.** Tako search is free on the gateway through 2026-09-30, so passes before
then are billed only for tokens. The pipeline must still price every search at list ($7 per 1,000 for Tako fast) so
the production projection is honest once the promotion ends.

## 2. The benchmark

### Reference

The agent pass `wr-full-1` (plus the ten `wr-pilot-1` rows): about 1,860 submissions that the ingest job accepted in
whole or in part on 2026-09-26 (1,854 whole, 12 partial, 6 refused). Its mix, from the ingest reports' totals: 525
removals (`not_ic`, `closed`, about 28%), 173 duplicates (about 9%), the rest `in_scope` or `not_found`; 6,183 facts
written, including one `research_source` record per cited page.

The reference is **another agent's work, not ground truth.** It is the bar to match, and disagreements are
adjudicated (§4) rather than scored against it blindly.

- A reference removal counts as a **strong** label only when it cites two different pages or one
  `government_registry`, `filing` or `certification_body` source. Many early removals rest on a single citation
  (commit ce49fcf: one batch removed 162 of 440 facilities that way); those are **weak** labels, reported separately.
- A reference finding counts only if ingest accepted it: the payload's assertions minus the items in the
  submission's `report.rejected`.

### Pipeline input: the record as it was before the agent researched it

`golden_facility` already contains the agent's findings, and about 28% of the reference facilities are no longer
in golden at all because the agent removed them. So the input for each facility is rebuilt by running
survivorship (`pipeline.golden.build_golden`, `registry/survivorship.yaml`) over that facility's `fact_assertions`
**excluding** source `web_research`. Reading today's golden row would leak the answers.

### Anchor set: people's word

Up to 50 facilities an ADL employee marked active (`adl_validated`, employee feedback), whether or not the agent
researched them. The pipeline removing any of these is an automatic fail, independent of the reference.

### Freezing and splitting

A read-only workflow run exports the benchmark once as an artifact (`benchmark.json` and its SHA-256). Every pass
names that run id and hash. The benchmark is **never committed to git** (the repository is public) and **never placed
in a model prompt**: the scorer reads reference answers only after the pipeline has finished a batch.

Stratified by reference verdict, strong/weak removal and whether the input has a website, with a fixed seed:

| Split | Size | Use |
| --- | --- | --- |
| Dev A | 25 | The fixed tuning batch. Every pass that changes something is compared on it. |
| Dev B–F | 5 × 25 | Fresh batches for generalisation checks, one every third pass. |
| Holdout | 100 | Scored only at a gate, at most twice. The loop agent sees only its aggregate scorecard and the listed failures. |
| Anchor | ≤ 50 | Safety only: scored at every gate. |

Removals and duplicates are over-sampled (about 30% and 15% of each batch) so the safety metrics have enough cases.

## 3. The pipeline under test (v0 specification)

One facility at a time, every step logged to the pass artifact:

1. **Crawl the known website** (free): homepage plus up to eight same-host pages whose link text or path mentions
   contact, about, location, plant, facility, capabilities or products. Respect robots.txt, a 20 s timeout and a 2 MB
   page limit, reusing `pipeline/web_research/run.py` `fetch()`.
2. **Search only when needed** (paid): when there is no website, the site is dead, or step 1 cannot anchor the plant
   (no page mentions this city or street). One request per facility, provider chosen per pass (§5).
   **Search results are cached per (facility, provider, query) for the whole evaluation**, so a pass that changes only
   extraction re-uses them and pays nothing for search.
3. **Deterministic extraction** (free): phones, ZIPs, emails and address lines near the facility's city or street,
   read from page text with regular expressions.
4. **Model judgement** (cheap): one call with only the relevant passages (at most about 6,000 input tokens) returns
   the verdict, capability, material and any literal field the regex step missed, each with a source URL and a quote.
5. **Quote check** (free): every quote must occur verbatim (whitespace-normalised) in the text of the page the pipeline
   fetched. A finding that fails is dropped before submission; the model is never trusted on this.
6. **Contract check** (free): the submission JSON is run through `pipeline.web_research.ingest.plan()` offline, exactly
   as ingest would judge it. Nothing is inserted during the evaluation.

Verdict policy: `not_ic` and `closed` only with evidence that already meets ingest's removal rule, otherwise
`not_found`. `duplicate` only with the other facility's id, found through the duplicate query in
`docs/web-research-agent.md` §3.

## 4. Metrics and gates

Literal fields are `name, address, city, state, zip, phone, email, website`. Values are compared normalised: the last
ten phone digits, the five-digit ZIP, the website host, addresses through
`pipeline.recovery.streets.street_equivalent` plus the house number, and names by token overlap of at least 0.8.

| Id | Metric | Holdout gate |
| --- | --- | --- |
| **S1** | Wrong removals: pipeline `not_ic`/`closed` where the reference says `in_scope`, or on an anchor | **0** |
| S2 | Findings ingest would refuse, as a share of findings submitted | ≤ 5% |
| S3 | Quotes not found verbatim in the fetched page | 0 (by construction; the scorer re-checks) |
| V1 | Verdict agreement on reference `in_scope` rows | ≥ 85% |
| V2 | Strong reference removals the pipeline also removes, or holds as `not_found` | ≥ 80%, removing ≥ 50% |
| V3 | Reference duplicates the pipeline also flags, with the same survivor | reported (target ≥ 50%) |
| F1 | Agreement where both assert the same literal field | ≥ 90% |
| F2 | Coverage: reference literal findings the pipeline also asserts | ≥ 70% |
| F3 | Pipeline-only literal findings (novel), with adjudicated precision | reported; ≥ 90% precision to count |
| J1 | Capability and material agreement | reported only: both sides are judgement, and web judgement only fills blanks |
| C1 | List-price cost per facility | ≤ $0.010 (stretch $0.005) |
| C2 | List-price cost per accepted literal fact | reported |
| T1 | Runner minutes per facility | reported |

**Match** means every gated metric passes on the holdout. **Exceed** means match, plus either F2 ≥ 100% or at least
10% more accepted literal facts per facility than the reference, with F3 precision ≥ 90%.

### Adjudication of disagreements

Where both sides assert the same field with different values (F1 misses), and for F3 novel findings:

1. **Deterministic first.** If only one side's quote contains its value, or only one side's page is still reachable
   and still states it, that side wins.
2. **A judge model for what remains**, capped at $0.25 per pass: one mid-tier model (for example
   `google/gemini-3-flash`) sees both claims, both quotes, both URLs and freshly fetched page text, and answers
   `pipeline`, `reference`, `both` (both true, for example a head office and a plant) or `neither`.
3. **A person at each gate.** Ten random adjudicated cases per gate go to the user; if the user overturns two or more,
   the judge is not trusted and the gate is re-scored by hand.

## 5. Model and search ladder

Prices are from the gateway catalog on 2026-09-26 (`https://ai-gateway.vercel.sh/v1/models`), per million tokens.
**Each pass re-reads the catalog and records the prices it used**; prices change.

| Tier | Models (input / output $ per 1M tokens) | Use |
| --- | --- | --- |
| 0 | `openai/gpt-oss-20b` 0.03/0.14 · `deepseek/deepseek-v4-flash-0731` 0.076/0.153 · `openai/gpt-5-nano` 0.05/0.40 · `google/gemini-2.5-flash-lite` 0.10/0.40 · `openai/gpt-oss-120b` 0.10/0.50 | Start here |
| 1 | `google/gemini-3.1-flash-lite` 0.25/1.50 (the current Tako pipeline's model) · `openai/gpt-5-mini` 0.25/2.00 | Only for a stage whose errors a pass has shown are the model's fault |
| 2 | `google/gemini-3-flash` 0.50/3.00 · `anthropic/claude-haiku-4.5` 1.00/5.00 | Adjudication only, never in the pipeline |

Excluded: free, stealth and preview models (availability and behaviour change without notice, so results do not
reproduce), and anything priced above Tier 2 without the user's approval.

Search tools (any model, gateway server tools, per 1,000 requests): **Perplexity $5**, **Parallel $5** (10 results
included), **Exa $7**, **Tako fast $7** (free through 2026-09-30), Tako deep $12. Provider-native search is priced per
model and is not used. Deep or multi-search modes need the user's approval.

A 6,000-token prompt with an 800-token answer costs about $0.0006 on `gpt-5-nano`, so **search dominates**. The
cheapest pipeline is the one that searches least: the crawl and cache levers matter more than the model choice.

## 6. Pass protocol

- A pass tests **one change** against the previous best configuration on Dev A (25 facilities), or checks
  generalisation on the next fresh Dev batch (every third pass).
- A change is kept only if it improves the primary score without worsening any safety metric. The primary score is
  F2 × F1, with S1 as a veto.
- Every pass appends one line to `research/pipeline-eval/passes.jsonl` on the evaluation branch: pass id, commit,
  benchmark hash, change tested, batch, facilities, billed $, list $, cumulative $, and the §4 metrics.
- **Holdout gate:** when Dev A and the last fresh batch both clear the gate thresholds, or after three passes in a row
  that each improve the primary score by less than 2 points. A failed gate returns to tuning once; a second failure
  ends the evaluation.

## 7. What the evaluation produces

A report to the user with the final configuration (models, search tool, levers), the holdout scorecard beside the
reference, the adjudication sample, and the projected list-price cost for the roughly 4,500 golden facilities that
`wr-full-1` has not researched. Production runs, which insert submissions and so write to the warehouse through
ingest, are a separate decision for the user.
