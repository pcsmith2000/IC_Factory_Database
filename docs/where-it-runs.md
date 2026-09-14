# Where it runs

GitHub is the system of record and the orchestrator. Google Cloud is compute and storage.
They connect through Workload Identity Federation — no long-lived key is stored anywhere.

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

- **GCS bucket** (`registry/config.yaml → storage.gcs_bucket`): raw archives at
  `ic-sources/<source>/<date>_raw.*`, bulk files fetched at run time, classifier response
  archives. Never in git.
- **Cloud Run Jobs** for the heavy steps when the Actions runner is not enough (6-hour cap,
  7 GB): the EPA FRS bulk parse, Playwright sweeps of the certification directories,
  classifier batches. The workflow launches the job and waits; the container is built once
  from this repo's `pyproject.toml`.
- **Secret Manager** for `ANTHROPIC_API_KEY`, `CENSUS_API_KEY` when running in Cloud Run;
  GitHub secrets when running on the runner.
- **BigQuery** as the eventual warehouse. Today Layer 8 loads the same star schema into a
  SQLite file in `build/` (`docs/warehouse.md`); `BQ_DATASET` switches engines once the
  BigQuery loader exists.

## Setup once

1. Create the bucket and a service account with `storage.objectAdmin` on it.
2. Configure Workload Identity Federation for this repo; set repo variables
   `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT`.
3. Add repo secrets `ANTHROPIC_API_KEY`, `CENSUS_API_KEY`.
4. Pick one scheduler. This repo keeps the cron in Actions so the run, its failure and its
   release PR are in one place; Cloud Scheduler is not used.

## Cowork's role

The prototype keeps evolving in Cowork: editing the registry when a source is found, working
the review queue with a model's help, diagnosing a gate failure, building the missing layers.
`pipeline/compare.py` diffs a Cowork build against a GitHub release so drift between the
prototype and the fixed process is a number:

    python -m pipeline.compare build_cowork/ build_github/
