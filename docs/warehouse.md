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

`dim_field` holds a row for every `field_key` `fact_assertions` can carry: each golden field, with
the order survivorship ranks it by (`fields:`, else `derived:`, else `default_order`), and each
operational flag listed under `flag_fields:` in `registry/survivorship.yaml` (e.g.
`geocode_quality`), with `survivorship_order_json` NULL because it never reaches golden. A new
flag-only field must be listed there; `tests/test_warehouse.py` fails on any orphan `field_key`.

## Permanent facility IDs (epic #39)
An IC-number is minted once, in the warehouse, and never reissued. `id_registry.json` used to be
the registry, and every pipeline run minted into its own copy on its own branch, so forks handed the same
numbers to different plants (21 releases, 17 registries, 2,286 IC-numbers naming a plant in
another state). The registry is now four tables and a counter, owned by `pipeline/facility_registry.py`:

```
facility            facility_id "IC-00001", status active | merged | retired, merged_into
facility_match_key  match_key (a reconcile signature) -> facility_id; many keys per plant, one plant per key
facility_event      seed · mint · merge · split · retire · repoint, with actor and reason
legacy_id_map       (registry hash, historical IC-number) -> permanent facility_id   (#42)
facility_id_seq     Postgres sequence (SQLite: facility_id_counter), starts at IC-96841,
                    above every number any registry ever issued; never lowered
```
The 6,426 facilities in golden on 2026-09-24 keep their numbers (the owner's decision). Seed and
inspect, on main only:
```
python -m pipeline.facility_registry seed --dry-run    # coverage and collisions, writes nothing
python -m pipeline.facility_registry seed              # idempotent
python -m pipeline.facility_registry status
```
(or the `facility-registry` workflow).

**Legacy crosswalk (#42).** `fact_assertions` keeps the IC-number each release used and is never
rewritten. `python -m pipeline.legacy_ids crosswalk` fills `legacy_id_map` with one row per
(registry hash, historical number) → permanent `facility_id`, plus `release_registry` (release
tag → registry hash). Resolution is per registry, because within one registry a number always
names one plant. Rules are tried in order: `current` · `same_id` · `identity` (shared
name+city+state) · `unique_name` (the only plant with that name in that state, and one side names
no city) · `address` (a numbered street in the same state). Anything else, including every
ambiguous case, is `unresolved`, with a NULL facility. The map is derived and a re-run replaces it.
Read facts through **`v_assertions_resolved`**, which adds `permanent_facility_id` (following one
`merged_into` hop) and `resolve_method`. On 2026-09-24 it resolved 90.8% of 743,681 fact rows. The
rest belong to plants golden no longer holds.

**Resolving and minting (#43).** Once the registry is seeded, Layer 5 resolves every cluster
through it (`facility_registry.DbIdRegistry`). The steps are tried in order:
1. **known**: the cluster's signature is a key, so it gets that facility (following a merge).
2. **attach**: the signature is new, but its rows' other signatures name exactly one live
   facility. The plant keeps its number, for example when it's respelled or gains an address.
3. **adopt**: `id_registry.json` already numbered this signature. The plant is registered under
   that old number, which covers the ~90,000 T0 leads and excluded rows the seed didn't register.
4. **mint**: otherwise, a new number comes from `facility_id_seq`.

Keys are only ever added. If a key would re-point to another facility, the run halts at Layer 5,
which is the new G3 guarantee alongside "a re-run issues 0 ids". Every step writes a
`facility_event`. `id_registry.json` is then an export: a superset that is never pruned, whose
`next` never falls behind the sequence. A scratch registry (`IC_ID_REGISTRY`, which every run
off main uses) or an unseeded warehouse still uses the file.

Operator acts, each an event with `--actor` and `--reason`:
```
python -m pipeline.facility_registry merge IC-00002 --into IC-00001 ...   # one plant, two numbers
python -m pipeline.facility_registry retire IC-00003 ...                  # not a plant; never reissued
python -m pipeline.facility_registry repoint "TX|S|1 main st" --to IC-00004 ...
python -m pipeline.facility_registry mint ...                             # a split = mint + repoint
```
A merge always points at a live root and re-roots anything merged into its source, so
`v_assertions_resolved` resolves every fact in a single hop.

## Continuous golden (#44)
Golden no longer waits for a pipeline run. A statement trigger on `fact_assertions` records every
(facility_key, release_tag) that gains a fact in **`golden_dirty`**, and so does a facility merge.
**`python -m pipeline.golden_refresh`** drains it; the `golden-refresh` workflow runs it every 15
minutes on main. For each queued facility it:

1. resolves it to its **permanent** facility (release registry → `legacy_id_map`, or directly for a
   release loaded through the registry), following one `merged_into` hop;
2. reads the **golden basis** through `v_assertions_resolved`: the current release's facts, plus
   carried enrichment, Tako, ASTRA and employee-feedback facts from any release, but only for a
   plant the current release still asserts something about, so a dropped plant stays dropped.
   Promote reads exactly this basis through the same function (`compute_full`, #49);
3. applies `golden.build_golden` and the E10 state gate, exactly as promote does, and upserts or
   removes that one golden row.

Guarantees:
- It only touches **registered** facilities. An unresolvable key, such as an `ADL-*` facility an
  employee created in ADL_Viz, is left as it is and reported.
- A facility that would **lose** a field it carries isn't written. It stays queued and is
  reported, unless E10 withheld the field or a person ruled the plant not IC.
- Merged and retired numbers leave golden.
- `--all` (a full rebuild through the same path) and any sequence of incremental refreshes give
  the same table, and this is tested.

On 2026-09-24 a dry-run `--all` against live golden left 6,390 of 6,426 rows identical, changed 36
(all gains, or rooftops replacing place-level pins) and lost nothing.

```
python -m pipeline.golden_refresh --dry-run            # what the queue would change
python -m pipeline.golden_refresh --all --dry-run      # the whole table, compared with golden now
```
**Every writer now goes through one path (#49):**
- **Promote** builds golden with `compute_full`, so it can't disagree with the refresh. A dry run on
  2026-09-25 matched live golden exactly.
- **Layer 8** still writes golden from the run's own rows inside the load transaction. Once the
  registry is seeded, `load_release` immediately runs a full refresh for the new release, so
  carried enrichment is restored in the same call and the un-enriched table is never left standing.

**Release snapshots.** Golden is live, so a release is a frozen copy: **`golden_release`**
(release_tag, facility_key, snapshot_at, row_json). `load_release` writes one for every release on
`main`. To freeze golden by hand:
```
python -m pipeline.golden_refresh --snapshot    # golden as it stands, under its release tag
```

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
