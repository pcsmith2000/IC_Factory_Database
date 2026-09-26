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

1. **The budgeted gateway key.** The repository secret `AI_GATEWAY_API_KEY` now routes to the key the user
   allocated for this work, and its gateway limit is the backstop. The key is **shared**: `tako-*`, `capability`,
   `bakeoff` and `enrich` workflows use it too, and the quarterly `pipeline-run` (next on 2026-10-01 12:00 UTC) triggers
   `enrich`, whose AI `locate` stage researches up to 200 facilities with it. The ledger counts only evaluation spend,
   so before each pass check that no other workflow using the key is running.
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
2. **A judge model for what remains**, capped at $0.25 per pass: a stronger model than the pipeline's (for example
   `google/gemini-3-flash`) sees both claims, both quotes, both URLs and freshly fetched page text, and answers
   `pipeline`, `reference`, `both` (both true, for example a head office and a plant) or `neither`.
3. **A person at each gate.** Ten random adjudicated cases per gate go to the user; if the user overturns two or more,
   the judge is not trusted and the gate is re-scored by hand.

## 5. Models and search: the option space

The agent chooses; this section says what is available and what it costs. Prices are from the gateway catalog on
2026-09-26 (`https://ai-gateway.vercel.sh/v1/models`, browsable at https://vercel.com/ai-gateway/models).
**Each pass re-reads the catalog and records the prices it used**; prices change.

### Hosted models, cheapest first

Cost of one judgement call (6,000 input tokens, 800 output tokens). "Open" marks open-weight families; confirm the
licence on the model page before relying on it.

| Model | Open | $ in / out per 1M | $ per call |
| --- | --- | --- | --- |
| `inclusionai/ling-3.0-flash` | yes | 0.021 / 0.063 | 0.00018 |
| `alibaba/qwen3.7-flash` | yes | 0.030 / 0.130 | 0.00028 |
| `openai/gpt-oss-20b` | yes | 0.030 / 0.140 | 0.00029 |
| `amazon/nova-micro` | | 0.035 / 0.140 | 0.00032 |
| `inception/mercury-2.5` | | 0.040 / 0.150 | 0.00036 |
| `mistral/mistral-nemo` | yes | 0.040 / 0.170 | 0.00038 |
| `nvidia/nemotron-3.5-lightning` | yes | 0.050 / 0.200 | 0.00046 |
| `deepseek/deepseek-v4-flash-0731` | yes | 0.076 / 0.153 | 0.00058 |
| `openai/gpt-5-nano` | | 0.050 / 0.400 | 0.00062 |
| `zai/glm-4.7-flash` | yes | 0.070 / 0.400 | 0.00074 |
| `google/gemini-2.5-flash-lite` | | 0.100 / 0.400 | 0.00092 |
| `openai/gpt-oss-120b` | yes | 0.100 / 0.500 | 0.00100 |
| `google/gemini-3.1-flash-lite` (today's Tako pipeline) | | 0.250 / 1.500 | 0.00270 |
| `openai/gpt-5-mini` | | 0.250 / 2.000 | 0.00310 |
| `google/gemini-3-flash` | | 0.500 / 3.000 | 0.00540 |
| `anthropic/claude-haiku-4.5` | | 1.000 / 5.000 | 0.01000 |

Any of these, or any other catalog model, may be tried, including routing only the hard cases to a stronger model,
as long as the configuration's average cost stays inside the C1 gate. Models priced above $2 per million input or $10
per million output tokens need the user's approval.

**Free and stealth models** (`-free`, `stealth/…`, preview builds) may be tried for exploration, but cannot be the
recommended production configuration: their availability, limits and behaviour change without notice.

### Open-weight models on the runner

The repository is public, so GitHub-hosted runners (4 vCPU, 16 GB) cost nothing. An open-weight model served inside
the job (for example with Ollama or llama.cpp, the weights cached between runs) has **zero token cost** but is slow on
CPU. It is a legitimate candidate: a pass measures its quality and its runner minutes per facility (T1).

### Search tools

Gateway server tools usable with any model, per 1,000 requests: **Perplexity $5**, **Parallel $5** (10 results
included), **Exa $7**, **Tako fast $7** (free through 2026-09-30), Tako deep $12. Provider-native search is priced per
model. Deep or multi-search modes need the user's approval.

**Search dominates the cost.** One search costs $0.005 to $0.007; the cheapest judgement calls above cost a few
hundredths of a cent. The cheapest pipeline is the one that searches least: crawling known sites, free website
discovery and the evaluation-wide search cache matter more than the model.

## 6. Pass protocol

The agent designs its own passes. These rules keep the results comparable:

- A pass compares a candidate configuration with the best so far on the same facilities, normally Dev A. Change one
  thing when you can; when you change several, say so and expect to separate them later if the result is surprising.
- Check generalisation on a fresh Dev batch regularly (about every third pass), because Dev A will be over-fitted.
- A change is kept only if it improves the primary score without worsening any safety metric. The primary score is
  F2 × F1, with S1 as a veto.
- The §3 specification is the starting point, not a constraint: re-architecting steps, adding free sources, or
  splitting work between models is in scope, within the §1 budget and the §4 gates.
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
