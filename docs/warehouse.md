# Warehouse — star schema; SQLite locally, Postgres (Neon) in the cloud

## Engine
One loader (`pipeline/warehouse.py`), one schema, two engines:

| engine | when | where the data lives |
|---|---|---|
| **sqlite** | no `DATABASE_URL` in the environment (laptop, Cowork) | `warehouse.sqlite_path` in `registry/config.yaml`, default `build/ic_factory.sqlite`; a build artifact, never committed |
| **postgres** | `DATABASE_URL` set (GitHub Actions secret, or `neon env pull` locally) | the Neon project; persists across runs, so `fact_assertions` really is append-only across releases |

The SQL is written once in the dialect both share (`ON CONFLICT` upserts, `TEXT / INTEGER / REAL`);
only the parameter placeholder differs. Selection order: `IC_WAREHOUSE_ENGINE` env ·
`DATABASE_URL_UNPOOLED` / `DATABASE_URL` env (→ postgres) · `warehouse.engine` in config
(default sqlite). `engine: none` disables the load. An engine that is selected but cannot be
opened (no URL, driver missing, connection refused) halts the run at Layer 8 — never skipped.

**Neon.** The loader prefers `DATABASE_URL_UNPOOLED` (direct connection): it runs DDL and one
transaction per release, which does not belong on the PgBouncer transaction-mode pool. Both
strings come from `neon env pull` locally and are set as repository secrets for `run.yml`.
Neon scales to zero between quarterly runs. `python -m pipeline.warehouse init` creates the
schema on an empty database (idempotent; the loader also does this on open).

**Google Cloud later.** The path is Cloud SQL for PostgreSQL (or AlloyDB): `pg_dump` from Neon,
restore, change the connection string, nothing else. If column analytics are ever wanted,
BigQuery federated queries read Cloud SQL Postgres in place — the star schema stays where it
is. `docs/gcp-setup.sh` remains for the raw-archive bucket and Workload Identity Federation;
its BigQuery dataset step is no longer on the path.

## Schema — a star with one derived table
```
fact_assertions        (assertion_id, facility_key, source_key, field_key, date_key, release_tag,
                        value, basis, site_visit, row_hash, confidence)          ← the fact; append-only
dim_facility           (facility_key, facility_id "IC-00001", signature, first_seen_release)
dim_source             (source_key, source_id, class, method, status_basis, status)
dim_field              (field_key, field, survivorship_order_json, survivorship_version)
dim_date               (date_key, date, quarter, year)
golden_facility        derived: survivorship over fact_assertions — materialised view or scheduled
                       query; never written directly; one row per facility, every field + __source
fact_release_metrics   (release_tag, run_ts, published_count, raw_count, dup_rate, recall,
                        mean_abs_bias, gates_passed, registry_version, survivorship_hash, prompt_hash, model)
ref_control            versioned; control_id, name, city, state, triage, reason, checksum
ref_source_registry    versioned snapshot of registry/sources.yaml per release
ref_known_gaps         state, cause, as_of
ref_source_row         provenance anchor: row_hash → source_url, source_document, row_position,
                       source_identifier, verbatim columns, facility_key, match_method/confidence
conflicts              derived per release: facility, field, winner, values
v_golden_field         view: golden_facility unpivoted to (facility, field, value, source)
v_provenance           view: golden value → the assertion(s) that carried it → the contract row
```
The golden table is not a second fact: it is a function of the fact and `dim_field`'s
survivorship order. Reference tables are small and versioned, not dimensional.

## Loader (Layer 8)
`build/assertions.csv` → `fact_assertions` (append, tagged with release; `assertion_id` is a hash
of facility · field · value · source · date · row_hash, so re-loading a release is a no-op);
`build/golden.csv` → `golden_facility` (replace); `build/conflicts.csv` → `conflicts`;
`build/rows_reconciled.csv` → `ref_source_row` (upsert on row_hash); run record →
`fact_release_metrics` (append). Dimensions upsert; `dim_facility` keeps `first_seen_release`.
The registry, control list (keyed by its checksum) and known gaps are snapshotted per release.

## Provenance — the question the warehouse exists to answer
"Where did this address come from?" has an exact answer at every step:

```
golden_facility.address, address__source      the value and the source that won survivorship
  → v_golden_field                            same, one row per (facility, field)
  → fact_assertions                           the assertion: date, basis, site_visit, confidence, row_hash
  → ref_source_row                            the contract row: source_url, source_document, row_position
  → dim_field.survivorship_order_json         why that source won (the rule that ranked it)
  → conflicts                                 what the other sources said instead
```

```
python -m pipeline.warehouse provenance IC-00001               # every field, source, document, row
python -m pipeline.warehouse provenance IC-00001 --field address
python -m pipeline.warehouse releases                          # fact_release_metrics
python -m pipeline.warehouse sql "select state, count(*) from golden_facility group by 1"
```

A human correction is an `operator` assertion with no row_hash: `v_provenance` shows it with
empty document columns, which is the honest answer. Nothing in the warehouse is edited by hand;
a wrong golden value is an assertion not yet recorded in `control/operator_assertions.csv`.

## Neon — set up once
1. `npx neon@latest init --agent` (or `neon auth` + `neon link`) in the repo; `.neon` is git-ignored.
2. `neon env pull` → `.env.local` with `DATABASE_URL` and `DATABASE_URL_UNPOOLED`.
3. `python -m pipeline.warehouse init` — schema on the default branch.
4. Credentials for `run.yml`, either of:
   - repository secrets `DATABASE_URL` and `DATABASE_URL_UNPOOLED` (the two strings `neon env pull` writes), or
   - the Neon GitHub integration (Neon console → project → Integrations → GitHub), which stores
     `NEON_API_KEY` (secret) and `NEON_PROJECT_ID` (variable); the workflow derives both URLs from
     them at run time (`.github/scripts/neon_connection_string.py`). A stored `DATABASE_URL` wins.
   The loader needs a role that can CREATE in `public` and INSERT/UPDATE/DELETE — the project's
   default owner role has this.
5. Optional: a Neon branch per experiment (`neon checkout dev-survivorship-v2`) to try a
   survivorship or normalisation change against a copy of the release without touching it.

## Raw archives — Vercel Blob
Not in the warehouse: the files Layer 1 fetched go to a private Vercel Blob store
(`pipeline/archive.py`, `archive:` in config) keyed `ic-sources/<source>/<date>/<file>`, with a
`manifest.json` (size, sha256, blob URL) per source and date. `ref_source_row.source_document`
plus the run date is the key into it. Secret: `BLOB_READ_WRITE_TOKEN`.

## GCP resources to create once (later, for Cloud Run Jobs)
1. ~~GCS bucket~~ — raw archives live in Vercel Blob; a GCS uploader is a drop-in if that changes
2. ~~BigQuery dataset `ic_factory`~~ — superseded by Neon / Cloud SQL for PostgreSQL
3. Service account `ic-pipeline@<project>.iam.gserviceaccount.com`
4. Workload Identity Federation pool + provider trusting repo `pcsmith2000/IC_Factory_Database`
5. Secret Manager: `AI_GATEWAY_API_KEY`, `CENSUS_API_KEY`
6. (later) Cloud Run Job `ic-pipeline-heavy` built from this repo

## Roles on the service account
| Role | Why |
|---|---|
| `roles/storage.objectAdmin` on the bucket | archives, bulk files |
| `roles/bigquery.dataEditor` on the dataset | load tables |
| `roles/bigquery.jobUser` on the project | run load/query jobs |
| `roles/secretmanager.secretAccessor` | API keys in Cloud Run |
| `roles/run.developer` | only when heavy steps move to Cloud Run Jobs |
| `roles/iam.workloadIdentityUser` binding to the WIF principal for the repo | lets Actions assume the SA |

People: `roles/bigquery.dataViewer` on the dataset for anyone who reads the golden table.

## Repo variables and secrets
Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` (raw archives, later).
Secrets: `AI_GATEWAY_API_KEY`, `CENSUS_API_KEY`, `DATABASE_URL`, `DATABASE_URL_UNPOOLED`.
