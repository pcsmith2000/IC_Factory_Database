# Pipeline controls

The DB2.0 table and map show ingestion/refinement history, current stage, elapsed time,
classifier throughput, and recorded results. Progress is completed stages, not a duration
prediction. Runs made before telemetry was installed retain their GitHub status and link;
missing metrics are never displayed as zero cost or zero results.

## Deployment

1. Merge the corresponding `IC_Factory_Database` pipeline dashboard branch first. It adds
   the optional `dashboard_request` workflow input, serialization, and reporting hooks.
2. In Vercel's **ic-adl-viz** project, add server-only `ADL_VIZ_Pipeline_Controls` to the environments
   that should allow triggering runs. Use a fine-grained GitHub token limited to
   `pcsmith2000/IC_Factory_Database`, with **Actions: Read and write** and standard metadata read.
   Do not use a `NEXT_PUBLIC_` variable. Redeploy after adding it. The ChatGPT GitHub connection
   is not a credential available to the deployed application.
3. Existing `DATABASE_URL` and `ADL_FEEDBACK_PASSCODE` are reused. The PostgreSQL role must
   be able to create the two small telemetry/request tables. They are created idempotently
   on first report/request. No factory-table migration or data rebuild is required.
4. Deploy the VIZ branch. Unlock employee access, confirm the displayed run settings,
   and check that the run appears. A real run may incur provider charges and update live data.

[GitHub dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
requires Actions write permission. The endpoint accepts only the two allowlisted workflows
on main; users cannot send repository names, refs, model IDs or arbitrary workflow inputs.

## Run settings

- Ingestion: layers 1–8, AI enabled, model pinned by the registry; archived sources are used
  according to existing source configuration. Successful ingestion still automatically triggers
  the repository's existing **trial-branch** refinement. That trial does not publish to main.
- Refinement button: all seven stages, **main database**, AI ceiling 200 facilities, geocoding
  ceiling 2,000 lookups, existing pinned defaults for everything else.
- Both workflows share a GitHub concurrency group. UI/API refuse a second active run, and a
  database-backed request UUID prevents double submissions across Vercel instances. Timeouts
  have unknown acceptance and are not automatically retried. Pending requests hold a 10-minute
  guard; GitHub's actual active-run list remains the guard after that. Old request IDs never retry.

## Reporting and cost

`pipeline_run_events` is keyed by run ID, attempt and stage. Reports go to the release database
including reports from trial Neon branches, whose target is labeled separately. UI refreshes every
30 seconds; server reads are briefly cached (longer without a token to respect public rate limits).

Metrics compare consistent before/after warehouse snapshots; concurrent employee updates can be
included. They distinguish added, removed and changed factories, changed golden values, assertion
row delta, total factories and conflicts. Baselines contain field hashes, not employee text.

Model cost is an **estimate** from recorded usage and a snapshot of the AI Gateway model list's
per-token prices at reporting time: https://ai-gateway.vercel.sh/v1/models . Unknown model/usage,
pricing API errors or tiered pricing show unavailable. Search, cache pricing adjustments, storage
and GitHub runner charges are not included. Geocoding is shown as an upper bound using the existing
stage metric, since the free allowance is shared across a day. This is not a total invoice.

Telemetry failure does not fail ingestion. GitHub remains authoritative for job success/failure,
and expired sessions, rejected dispatches and stale telemetry are shown explicitly.

## Validation

`npm run test:pipeline` exercises authentication, CSRF, allowlisted inputs, database-backed
idempotency, active-run blocking, ambiguous dispatch failure, and stage progress.
`npm run build` checks the Next.js production build and TypeScript.
