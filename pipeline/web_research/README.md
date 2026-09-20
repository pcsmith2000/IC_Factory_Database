# Tako AI Search pass — read-only pilot

Manually run **Tako AI Search pass (read-only)** in GitHub Actions.

- `plan`: export the selected golden rows without search spend.
- `research`: select rows and research their basic information with Tako.
- `pilot`: research the frozen ten-row input, then compare with an independently researched reference.

The selection controls are `states`, `facility_ids`, `missing_fields`, `source_ids`, `tiers`, `release_tag`, `row_limit` (1–200), `row_offset`, and `sample_seed`. Different filter types intersect; comma-separated values within a filter are alternatives. `missing_fields` means missing **any** listed field. Blank filters include all eligible rows. Sampling uses a stable hash of facility ID and seed, not database order. Offset pagination is only stable while the eligible database cohort is unchanged; use exact IDs or the frozen pilot for comparisons.

The release filter selects only rows still present in the golden table; it does not reconstruct historical releases. Source filtering requires evidence for that row's current release.

The database selection uses a server-enforced read-only transaction. Database secrets are available only during selection; the researcher receives only the gateway key. Nothing loads assertions, updates golden, registers sources, or writes enrichment caches. There is deliberately no write-enabled mode.

Download the run artifact for input, selection manifest, model responses, fetched evidence, per-field proposals, and summary. `candidate` means the literal value was found on a fetched source page with a location/contact anchor, not human approval or a guarantee of identity. Unfetchable evidence, directory sources, company/office contacts, and conflicts remain `review`. The model's source classification still requires review. Existing values are preserved in the report beside proposed values.

Search uses one gateway request per row, with up to two additional transport retries for transient failures. Each request uses Tako fast web search with eight results and a 3,000-token answer ceiling. Authentication/budget failures stop the run. Incomplete runs fail visibly and preserve completed rows. This is not the deeper critical-row verification pass.

The reference answers are read only after the researcher finishes and never included in its prompt. This ten-row cohort is a development test, not a population-wide accuracy estimate. Hart Housing's historical Elkhart plant remains unverified; a review/abstention is the expected safe result. The two state conflicts must be surfaced rather than silently corrected.
