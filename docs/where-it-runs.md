# Where it runs

GitHub is the system of record and the orchestrator. Neon holds the warehouse, Vercel Blob
the raw archives; Google Cloud (Cloud Run Jobs, later Cloud SQL) is the eventual compute home.
GitHub ↔ GCP connects through Workload Identity Federation — no long-lived key is stored anywhere.

## GitHub

- **Repo** holds code, `registry/sources.yaml`, the frozen prompt, `id_registry.json`, the
  control checksum, and one run record per run.
- **`ci.yml`** runs the tests, parses the registry, and verifies the control checksum on
  every push.
- **`run.yml`** is the quarterly trigger (cron on the 1st of Jan/Apr/Jul/Oct, or manual). It
  runs layers 1–8, uploads `build/` as a workflow artifact whatever happens, and on success
  opens a release PR carrying `id_registry.json` and the run record. **Merging the PR is the
  human sign-off** — G1 pairs and the review queue are in the artifact for exactly that.
- A gate failure turns the run red at the failing layer. That is the halt.

## Google Cloud

- **Raw archives: Vercel Blob** (`registry/config.yaml → archive`), when `BLOB_READ_WRITE_TOKEN`
  is set: every file Layer 1 fetched at `ic-sources/<source>/<date>/<file>`, private, plus a
  `manifest.json` per source and date with size and sha256 of every file. Files over
  `archive.max_file_mb` (the EPA zip) are recorded by hash only; the EPA fetcher archives the
  filtered slice it used instead. Never in git. Moving this to a GCS bucket later is one
  uploader class (`pipeline/archive.py`) — the key layout stays.
- **Cloud Run Jobs** for the heavy steps when the Actions runner is not enough (6-hour cap,
  7 GB): the EPA FRS bulk parse, Playwright sweeps of the certification directories,
  classifier batches. The workflow launches the job and waits; the container is built once
  from this repo's `pyproject.toml`.
- **Secret Manager** for `ANTHROPIC_API_KEY`, `CENSUS_API_KEY` when running in Cloud Run;
  GitHub secrets when running on the runner.
- **Warehouse: Neon (Postgres)** when `DATABASE_URL` is set — the persistent store across
  quarterly runs; a SQLite file in `build/` otherwise (`docs/warehouse.md`). Cloud SQL for
  PostgreSQL is the drop-in when the project moves to Google Cloud.

## Setup once

1. Create a private Vercel Blob store; copy its read-write token.
2. Add repo secrets `ANTHROPIC_API_KEY`, `CENSUS_API_KEY`, `BLOB_READ_WRITE_TOKEN` (Vercel Blob),
   and either `DATABASE_URL` + `DATABASE_URL_UNPOOLED` or the Neon GitHub integration (Neon).
3. Later, for Cloud Run Jobs: Workload Identity Federation for this repo; repo variables
   `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT`.
4. Pick one scheduler. This repo keeps the cron in Actions so the run, its failure and its
   release PR are in one place; Cloud Scheduler is not used.

## Cowork's role

The prototype keeps evolving in Cowork: editing the registry when a source is found, working
the review queue with a model's help, diagnosing a gate failure, building the missing layers.
`pipeline/compare.py` diffs a Cowork build against a GitHub release so drift between the
prototype and the fixed process is a number:

    python -m pipeline.compare build_cowork/ build_github/
