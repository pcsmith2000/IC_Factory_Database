# Tako AI Search research and reviewed assertions

Manually run **Tako AI Search pass (read-only)** in GitHub Actions.

- `plan`: export the selected golden rows without search spend.
- `research`: select rows and research their basic information with Tako.
- `pilot`: research the frozen ten-row input, then compare with an independently researched reference.

The selection controls are `states`, `facility_ids`, `missing_fields`, `source_ids`, `tiers`, `release_tag`, `row_limit` (1–200), `row_offset`, and `sample_seed`. Different filter types intersect; comma-separated values within a filter are alternatives. `missing_fields` means missing **any** listed field. Blank filters include all eligible rows. Set `missing_fields=all` explicitly to include complete rows as well; GitHub may replace an empty dispatch input with its default. Sampling uses a stable hash of facility ID and seed, not database order. Offset pagination is only stable while the eligible database cohort is unchanged; use exact IDs or the frozen pilot for comparisons.

The release filter selects only rows still present in the golden table; it does not reconstruct historical releases. Source filtering requires evidence for that row's current release.

The database selection uses a server-enforced read-only transaction. Database secrets are available only during selection; the researcher receives only the gateway key. These research modes do not load assertions, update golden, register sources, or write enrichment caches. A separate approved-assertion action is described below.

Download the run artifact for input, selection manifest, model responses, fetched evidence, per-field proposals, and summary. `candidate` means the literal value was found on a fetched source page with a location/contact anchor, not human approval or a guarantee of identity. Unfetchable evidence, directory sources, company/office phone and address contacts, and conflicts remain `review`. The model's source classification still requires review. Existing values are preserved in the report beside proposed values.

Search uses Gemini 3.1 Flash-Lite through the gateway; a second, search-free Gemini 3.1 Flash-Lite request extracts from fetched pages. Search uses one gateway request per row, with up to two additional transport retries for transient failures. The search request uses Tako fast web search with eight results and a 3,000-token answer ceiling; extraction is capped at 3,500 output tokens. The gateway can make multiple internal search calls; actual counts and reported costs are recorded, not inferred from row count. Up to twelve public pages can be fetched per row. Rows stop starting after 20 minutes, preserving partial results and reporting failure. Authentication/budget failures stop the run. Incomplete runs fail visibly and preserve completed rows. This is not the deeper critical-row verification pass.

The coordinate-recovery action adds at most one bounded follow-up per row when the first result lacks a trustworthy facility-address bundle. That follow-up searches OSHA, state approved-manufacturer/licensing records, and official factory-location pages; it can extract cited text from public PDFs. The plan reserves the worst-case two-search/two-extraction model estimate. Small city spelling corrections and a physical street/ZIP replacing a mailing address are eligible only when the same facility and state remain anchored. State changes, different branches, directories, offices, headquarters, and ambiguous manufacturing scope remain review.

The reference answers are read only after the researcher finishes and never included in its prompt. This ten-row cohort is a development test, not a population-wide accuracy estimate. Hart Housing's historical Elkhart plant remains unverified; a review/abstention is the expected safe result. The two state conflicts must be surfaced rather than silently corrected.

Pilot quality gates require all ten rows, all three known identity hazards held for review, no incorrect or unsupported candidate among the scored fields, and at least six correct websites and six correct plant phones among nine resolvable reference rows. Coverage includes review-only results and is reported separately from eligibility. Additional fields and identity semantics still need human review. These are development acceptance criteria, not production write authorization.

Before inference, the action fetches current input/output rates from Vercel’s model catalog and publishes `cost-estimate.json` and a job summary. Assumptions: 45,000 input tokens, 2,500 output tokens, and three Tako searches per row across the search and extraction stages. The range is a planning estimate, not a spending cap. The September 2026 Tako promotion expires automatically; standard fast search pricing is budgeted afterward. Actual reported gateway costs and search counts appear in `summary.json`. Plan mode makes no inference calls.


## ADL / 4ward campaign

`Tako ADL research campaign (read-only)` runs the frozen 241-facility selection from planning runs 35541907051 and 35541991622. Each round uses three independent batches of 100, 100, and 41 rows, with a 95-minute research budget per batch. The first round is run 35542280759. Subsequent manual dispatches specify `round` and **all** earlier campaign run IDs in `prior_runs` (comma-separated). Those artifacts must remain available; a missing download fails rather than silently discarding history. Campaign artifacts are retained for 30 days and should be archived locally before expiry.

Every round preserves the original golden values. Earlier proposals are labeled unverified search context. The model is asked to seek missing physical plant/contact details and freshly verify repeated claims. Round reports count unique `(facility, field, normalized value)` tuples beyond both the frozen baseline and every earlier round. A counted proposal must have a fetched supporting quote, an identity anchor, an official/registry source classification and facility scope for location fields; verified company contact fields are counted but retained for review. These are **supported discoveries**, not approval to write: unresolved conflicts and unverified identity semantics remain separate from candidate fills. Site/source classifications and address scope require inspection of the evidence before geocoding.

Stop only after two successive complete 241-row rounds each add fewer than ten supported details. Incomplete coverage, errors, or missing prior artifacts cannot prove diminishing returns. Review the results after each round and correct extraction/search failures before deciding whether the threshold is met. Keep all results artifact-only; no assertions, golden rows or database caches are changed. Every batch computes its own preflight estimate and reports actual model cost.

A four-worker trial increased unconfirmed search responses. Subsequent rounds use one worker per 100-row batch (three batches can run concurrently), with one delayed confirmation retry. The selected cohort and novelty accounting remain unchanged. `resume_run` skips completed facilities from an incomplete attempt of the same round; combine original and resume artifacts when auditing that round. `coverage_complete` checks the actual unique result IDs against all 241 frozen IDs, not only a success flag or summed job counts.

Campaign evidence caching reuses successful public-page snapshots for at most four hours, retaining their original retrieval time and source run. Failed/unreadable pages are not cached, and new search URLs are fetched normally. Every row still requires a newly confirmed Tako search. This reduces repeat downloads without treating old search claims as new evidence.

`pipeline.web_research.ledger` consolidates distinct primary-source proposals across rounds. Multiple values for the same facility field remain review. Its cumulative inventory is separate from the per-round novelty metric and does not authorize database writes.


## Approved assertion import

`Tako approved assertions` is a separate manual action. It consumes the explicitly reviewed
`research/adl-2026-09-20/approved-assertions.json`, not raw model candidates. `plan` runs a
read-only live check; `apply` requires that plan's run ID and exact SHA256. Current facility
name/city/state must still match the reviewed snapshot. Applicable competing values remain
held; agreeing assertions from another source may be retained as corroborating evidence.
Historical registry assertions outside the current release are excluded from the comparison.

The importer transaction writes `dim_source`, `ref_source_row`, and `fact_assertions` only.
The source ID and separate source class are `tako_ai_search`; its display name is **Tako AI Search**.
Source URL, quote when available, review note, evidence scope, and campaign runs are retained.
Stable assertion IDs make retries idempotent. Any changed preview, failed read-back, or golden
value change aborts the transaction. The workflow does not invoke promotion or run research.

`golden._rank` places this source below even unlisted sources, regardless of recency, class,
or basis. The corresponding source registration and enrichment carry-forward rule are part
of this PR and must be deployed before relying on later promotion/release rebuild behavior.
Research and rooftop actions remain read-only.

User-authorized import run **35547749508** committed and verified **179 assertions for 88
facilities**: 54 websites, 45 phones, 3 emails, 33 addresses and 44 ZIP codes. The receipt is
`research/adl-2026-09-20/assertion-import-receipt.json`. No rooftop coordinates or unresolved
conflicts were imported, and golden values were unchanged. Broad research remains paused.


## Publish reviewed cached rooftops

`Publish reviewed cached rooftops` revalidates the complete saved cache from run35545590293;
no external geocoding/search credentials are provided. `plan` reads a repeatable database
snapshot and records the exact proposed assertions and golden-cell updates. `apply` requires
the plan run and SHA256, shares the warehouse execution lock, and aborts if the snapshot changed.

Only reviewed matching rooftops can assert `lat_lon` under `geocode:geocodio`, basis `rooftop`.
Current facility identity and the winning address must match the reviewed/geocoded site.
Human coordinates, conflicting existing rooftops and unresolved address corrections are held.
The normal survivorship function also promotes previously approved Tako basics into blank
cells where that source wins. This is scoped promotion: unrelated facts/facilities are untouched.
All assertions, provenance and golden updates commit together, with exact read-back verification.
