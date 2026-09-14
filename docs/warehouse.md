# Warehouse — BigQuery schema and GCP access

## Engine
BigQuery. Append-heavy, read-heavy, quarterly, columnar; free at this scale; no server.
GCS holds raw archives; BigQuery holds the modelled tables; the golden table is what
downstream users query.

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
conflicts              derived per release: facility, field, winner, values
```
The golden table is not a second fact: it is a function of the fact and `dim_field`'s
survivorship order. Reference tables are small and versioned, not dimensional.

## Loader (Layer 8, when `BQ_DATASET` is set)
`build/assertions.csv` → `fact_assertions` (append, tagged with release); `build/golden.csv` →
`golden_facility` (replace); run record → `fact_release_metrics` (append). Dimensions upsert.

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
