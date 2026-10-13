# Google Cloud: a possible later move

None of this is on the current path. The warehouse is Neon Postgres (`docs/warehouse.md`), raw
archives are in Vercel Blob, and every job runs in GitHub Actions. This page keeps the plan in
case the project moves to Google Cloud.

## Warehouse

The path is Cloud SQL for PostgreSQL (or AlloyDB): `pg_dump` from Neon, restore, and change the
connection string. Nothing else changes. If column analytics are ever wanted, BigQuery federated
queries can read Cloud SQL Postgres in place, so the star schema stays where it is.
`gcp-setup.sh` in this folder still covers the raw-archive bucket and Workload Identity
Federation. Its BigQuery dataset step is no longer needed.

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
