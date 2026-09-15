# IC Factory Database

A database of every industrialized-construction (IC) manufacturing facility in the United
States, built by a fixed, end-to-end ingestion pipeline that refuses to publish unless every
assumption it rests on passes its own check.

**IC = industrialized construction** (modular, volumetric, panelised, precast, mass timber,
truss and component plants) — **not integrated circuits.**

## How it runs

One quarterly run. The source registry (`registry/sources.yaml`) declares what is pulled; the
pipeline pulls all of it, validates, classifies where no industry filter exists, resolves each
row to a legal entity, reconciles rows into facilities with stable IC-numbers, passes five
quality gates, measures itself against a Census frame and a held-out control list, and
publishes one tagged release. There is no "next source" decision anywhere in it — adding a
source is a versioned edit to the registry that takes effect next run.

```
Layer 0  reference     frame (Census CBP) · control (held out, checksummed) · source registry
Layer 1  acquire       classes A state registries · B national regulators · C rosters · D certification
Layer 2  validate      schema check · verbatim kept + normalised columns · drift vs last pull
Layer 3  classify      class B only — keyword × NAICS candidates → frozen prompt → IC / NOT-IC / UNCERTAIN
Layer 4  resolve       every row → legal entity (SoS · FMCSA · ICC-ES) before any matching
Layer 5  reconcile     cluster → facility · id_registry issues stable ids · tier by evidence
Layer 5b golden        rows → field-level assertions → survivorship rules → golden table (never edited directly)
Layer 6  gates         G1 dedupe · G2 false-merge · G3 id stability · G4 control isolation · G5 classifier eval
Layer 7  measure       recall on control · per-state coverage · bias index — reported, not steered
Layer 8  publish       tagged release: registry version + id_registry hash + control checksum + prompt/model versions
                       → warehouse: star schema, SQLite file today, BigQuery when the loader lands
```

Full design: `docs/pipeline-v4.md` (assumptions register inside). Data model: `docs/data-model.md`.
Where it runs: `docs/where-it-runs.md`. Warehouse (BigQuery star schema, GCP access): `docs/warehouse.md`, `docs/gcp-setup.sh`. Runbook: `docs/runbook.md`. Contract: `docs/contract.md`.

## Deterministic vs AI vs human

| Step | Kind |
|---|---|
| Control triage (once per list edit) | AI proposes · human signs off |
| Acquire, classes A · B | deterministic |
| Acquire, classes C · D | deterministic fetch · AI extraction into the contract schema |
| Classify | AI — frozen prompt, pinned model, temperature 0, schema-validated output |
| Review queue (UNCERTAIN rows) | human |
| Resolve entity | deterministic lookups · AI adjudicates the residual only |
| G1 pair review | human |
| Everything else | deterministic |

Every AI call records its prompt hash and model id in the run record, so a re-run is
comparable to within the seeded-eval tolerance.

## Layout

```
registry/sources.yaml          the fixed input — every known source, class, method, traps, status
registry/survivorship.yaml     which source wins per golden field; hashed into the release tag
registry/known-gaps.yaml       out-of-band states with the cause on record
prompts/CLASSIFIER-PROMPT.md   frozen; changing it is a versioned change that G5 must re-pass
control/                       triaged control list + checksum · seeds for G5 · crosswalk · operator assertions
pipeline/                      one module per layer, plus gates.py, warehouse.py and run.py
pipeline/sources/              one fetcher per source id (fetch → archive → parse); _common.py toolkit; check.py harness
prompts/EXTRACTION-PROMPT.md   frozen; AI transcription of prose location pages (corporate_locations)
ic-csv/                        contract CSVs, one per source per run (generated)
run_records/                   one JSON per run: inputs, versions, gate results, metrics
build/ic_factory.sqlite        the warehouse (generated): assertions, golden table, provenance views
docs/                          pipeline design, assumptions register, contract, runbook
.github/workflows/             ci.yml (tests on every push) · run.yml (quarterly pipeline run)
```

Raw source archives and bulk downloads are **not** in git — they go to the GCS bucket named
in `registry/config.yaml`, keyed `ic-sources/<source>/<date>_raw.*`.

## Standing rules

1. Never fabricate a row. CA has zero rows instead of plausible ones.
2. Verbatim in, normalised out. Source typos are preserved; normalised columns are added, never
   substituted.
3. Every row carries provenance: source URL, document, retrieval date, row position.
4. A match is a decision, and decisions are recorded — method, confidence, log entry.
5. A published number carries its release tag or it is not quotable.

## Running locally

```
pip install -e ".[dev]"
python -m pipeline.run --registry registry/sources.yaml --dry-run      # plan only
python -m pipeline.run --registry registry/sources.yaml --layers 2-8   # from existing ic-csv/
python -m pipeline.sources.check tx_tdlr                              # one source: fetch, parse, validate
python -m pipeline.warehouse provenance IC-00001                       # golden fields → source → document row
pytest
```

## Status (v1.0)

Scaffold of the v4 process. Layers 2, 5, 5b, 6 (all five gates), 7 and 8 run end to end
against the contract, and Layer 8 loads the SQLite warehouse (`docs/warehouse.md`); Layer 3 carries the regimented classifier call; Layer 4 (entity
resolution) passes rows through flagged NOT-ATTEMPTED and reports resolution_rate = 0 so the
gap is visible; Layer 1 has a fetcher for every active source (`docs/sources.md`), written against
endpoints found by search and not yet run live — the first `python -m pipeline.sources.check <id>`
on a networked machine is the check. The measured figures in the
docs are from the 2026-09-09 build (5,026 facilities after dedupe, 96% in-scope recall).
