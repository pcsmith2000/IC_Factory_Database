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

## Credentials — where each one goes

All of these are **repository-level**, in GitHub → Settings → Secrets and variables → **Actions**.
No job in `run.yml` or `ci.yml` declares an `environment:`, so GitHub *Environment* secrets would
not be visible to them; use the repository tab. Secrets and variables are different tabs on that
page — a variable in the Secrets tab (or the reverse) reads as empty, not as an error.

`run.yml` accepts either name in each row below, so whichever is set is the one used.

| name (either of) | tab | needed for | absent means |
|---|---|---|---|
| `BLOB_READ_WRITE_TOKEN` · `IC_DB_PRI_SOURCE_READ_WRITE_TOKEN` | Secrets | raw-source archive to Vercel Blob | no archive; raw files die with the runner (run record says so) |
| `BLOB_STORE_ID` · `IC_DB_PRI_SOURCE_STORE_ID` | Secrets | names the blob store explicitly | the store id is read out of the token |
| `DATABASE_URL` + `DATABASE_URL_UNPOOLED` · `IC_DB_DATABASE_URL`(`_UNPOOLED`) | Secrets | the Neon warehouse | falls back to the Neon integration below, else SQLite in the artifact |
| `NEON_API_KEY` | Secrets | set by the Neon GitHub integration; used only when `DATABASE_URL` is absent | — |
| `NEON_PROJECT_ID` | **Variables** | same integration path | — |
| `AI_GATEWAY_API_KEY` · `IC_DB_AI_GATEWAY_API_KEY` · `VERCEL_AI_GATEWAY_API_KEY` | Secrets | Layer 3 through the Vercel AI Gateway, and stage 9's `vercel:*_search` server tools | Layer 3 falls back to `ANTHROPIC_API_KEY` direct; stage 9 has no search and cannot run |
| `ANTHROPIC_API_KEY` · `IC_DB_ANTHROPIC_API_KEY` | Secrets | Layer 3 classification, AI extraction, the gateway's fallback | those layers fail loudly |
| `GEOCODIO_API_KEY` · `GEOCODIO_API` · `IC_DB_GEOCODIO_API_KEY` · `GEOCODIO_KEY` | Secrets | stage 10 geocoding | stage 10 refuses to start (the preflight names the key) |
| `CENSUS_API_KEY` · `CENSUS_KEY` | Secrets | Layer 7 frame refresh | frame is read from the committed CSV |
| `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` | Variables | later, Cloud Run Jobs | the GCP auth step is skipped |

Locally these are ordinary environment variables (`.env.local` from `neon env pull`, exported for
a shell). Check each one before it matters:

```
python -m pipeline.archive verify        # put a probe blob, read it back, delete it
python -m pipeline.warehouse init        # create/confirm the warehouse schema
python -m pipeline.control check         # the hand-placed inputs
```

## Setup once

1. Create a Vercel Blob store with **public** access — `registry/config.yaml → archive.access` says `public`, and a store created private rejects every write with *Cannot use private access on a public store*. Copy its read-write token.
2. Add repo secrets `ANTHROPIC_API_KEY`, `CENSUS_API_KEY`, `BLOB_READ_WRITE_TOKEN` (Vercel Blob),
   and either `DATABASE_URL` + `DATABASE_URL_UNPOOLED` or the Neon GitHub integration (Neon).
3. Later, for Cloud Run Jobs: Workload Identity Federation for this repo; repo variables
   `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT`.
4. Pick one scheduler. This repo keeps the cron in Actions so the run, its failure and its
   release PR are in one place; Cloud Scheduler is not used.

## Moving to another Vercel team

Two of the credentials above are Vercel-scoped and do not follow the repository: the Blob store and
the AI Gateway key. Everything else in the table is a third party (Anthropic, Neon, Geocodio,
Census) and is unaffected by which Vercel team the project sits in.

**1. The Blob store — the only irreplaceable thing.** It holds every raw source snapshot the
pipeline reads: 1,130 objects, 116 MB, under `ic-sources/`, `ic-runs/` and `ic-control/`. This is
not a cache. `archive.mode: blob-only` means Layer 1 reads the store and never scrapes, so a run
against an empty store halts at Layer 1 on the first source it cannot find (run 35276154465 did
exactly that with one missing source). Either move the store to the new team in the Vercel
dashboard, or create one there — **public access**, as above — and copy the objects across:

    python -m pipeline.archive verify        # proves the new token and store before anything else
    BLOB_READ_WRITE_TOKEN=<old> python -m pipeline.archive list <source_id>

Whichever route, the token changes. Put the new one in the repo secret; the store id is read out
of the token, so `BLOB_STORE_ID` only matters if you override it.

What survives regardless: the warehouse, `id_registry.json`, `run_records/`, the control file and
everything else under version control. Losing the store costs the snapshots, not the database —
but re-fetching them means re-scraping 20-odd sites, several of which are hand-placed uploads
(`docs/manual-uploads.md`) that cannot be re-fetched at all.

**2. The AI Gateway key.** Issue a new one in the new team. It pays for two things, and a run that
silently loses it behaves differently in each: Layer 3 falls back to `ANTHROPIC_API_KEY` direct and
keeps going (the run record names the provider, so check it), while stage 9 has no fallback — the
`vercel:parallel_search` / `vercel:tako_search` server tools exist only on the gateway.

**3. Check, do not assume, the Neon path.** `.github/scripts/neon_connection_string.py` talks to
`console.neon.tech` directly, so a Neon project owned by its own account is untouched by a Vercel
move. A Neon project provisioned *through* the Vercel marketplace integration belongs to the Vercel
team and moves with it, which changes `DATABASE_URL`. Run `python -m pipeline.warehouse init`
against the new value before trusting it.

**4. Re-point the repository secrets** (Settings → Secrets and variables → **Actions**, repository
tab, not Environments). Then prove it with a cheap dispatch rather than a full run:

    layers=1-2, ai=off        # reads the store, no model spend — fails fast if the token is wrong

## Cowork's role

The prototype keeps evolving in Cowork: editing the registry when a source is found, working
the review queue with a model's help, diagnosing a gate failure, building the missing layers.
`pipeline/compare.py` diffs a Cowork build against a GitHub release so drift between the
prototype and the fixed process is a number:

    python -m pipeline.compare build_cowork/ build_github/
