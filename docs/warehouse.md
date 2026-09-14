# Warehouse — star schema, SQLite today, BigQuery as the target

## Engine
**Today: SQLite** (`pipeline/warehouse.py`, standard library, one file at
`warehouse.sqlite_path` in `registry/config.yaml`, default `build/ic_factory.sqlite`). Layer 8
loads it on every successful release. The file is a build artifact — uploaded by `run.yml`,
never committed. Because the Actions runner is ephemeral, each GitHub run starts from an empty
file; a local or Cowork checkout accumulates releases across runs.

**Target: BigQuery.** Append-heavy, read-heavy, quarterly, columnar; free at this scale; no
server. GCS holds raw archives; BigQuery holds the modelled tables; the golden table is what
downstream users query. The schema below is the same for both engines, so the move is a second
loader, not a redesign. Setting `BQ_DATASET` in the environment (or `warehouse.engine: bigquery`)
selects BigQuery now — and halts the run at Layer 8 until that loader is written. It is never
silently skipped.

Engine selection, in order: `IC_WAREHOUSE_ENGINE` env · `BQ_DATASET` env (→ bigquery) ·
`warehouse.engine` in config (default `sqlite`). `IC_WAREHOUSE_PATH` overrides the SQLite path.
`engine: none` disables the load.

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

## GCP resources to create once (project owner/editor)
1. GCS bucket `ic-factory-database` (raw archives, bulk downloads, classifier response archives)
2. BigQuery dataset `ic_factory`
3. Service account `ic-pipeline@<project>.iam.gserviceaccount.com`
4. Workload Identity Federation pool + provider trusting repo `pcsmith2000/IC_Factory_Database`
5. Secret Manager: `ANTHROPIC_API_KEY`, `CENSUS_API_KEY`
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
Variables: `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `BQ_DATASET`.
Secrets: `ANTHROPIC_API_KEY`, `CENSUS_API_KEY`.
