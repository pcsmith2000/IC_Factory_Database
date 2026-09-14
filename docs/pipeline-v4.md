# IC Ingestion Pipeline — v4 (fixed end-to-end run)

Canonical copy lives in the Claude project doc `claude/ingestion-pipeline-v4.md` (2026-09-14);
this file tracks it. Diagram: the "IC Ingestion Pipeline Map" artifact.

The loop's job was discovery: find which sources exist, learn their traps, and test the
assumptions the database rests on. Eight iterations and the invisibility diagnostic did that.
v4 retires the loop. There is no "next source" decision anywhere in it. The source registry is
a fixed, versioned input; one run pulls all of it; and the run refuses to publish unless every
assumption the loop surfaced passes its own check.

## Layers

| Layer | Kind | What it does | Code |
|---|---|---|---|
| 0 Reference | DET · AI·HUMAN · HUMAN | Frame (Census CBP state totals, a floor) · control (triaged, checksummed, never merged) · source registry | `registry/`, `control/` |
| 1 Acquire | DET; DET·AI for prose/PDF sources | All active sources, every run → contract CSV + raw archive | `pipeline/acquire.py`, `pipeline/sources/` |
| 2 Validate + normalise | DET | Contract check · verbatim kept, `city_norm` / `street_key` / `state` added · drift vs last pull · NO-FIXED-PLANT · status basis | `pipeline/validate.py`, `pipeline/contract.py` |
| 3 Classify | AI (class B only) | keyword × NAICS candidates → frozen prompt → IC / NOT-IC / UNCERTAIN; seeds hidden in every batch | `pipeline/classify.py` |
| 4 Resolve entity | DET·AI | Every row → legal entity before matching (SoS · FMCSA · ICC-ES · crosswalk). **Not built**; rows pass through flagged | `pipeline/resolve.py` |
| 5 Reconcile | DET | Cluster → facility; stable IC-numbers; tier by evidence | `pipeline/reconcile.py`, `id_registry.json` |
| 5b Golden | DET | Rows → field-level assertions → survivorship rules → golden table + conflicts | `pipeline/golden.py`, `registry/survivorship.yaml` |
| 6 Gates | DET (G1 + human review) | G1 dedupe ≤ 2% · G2 false-merge · G3 id stability · G4 control isolation · G5 classifier P ≥ 95% / R ≥ 90% | `pipeline/gates.py` |
| 7 Measure | DET | Recall on control · per-state coverage · bias index (0.7–1.4), out-of-band with cause | `pipeline/measure.py`, `registry/known-gaps.yaml` |
| 8 Publish | DET | Tagged release: registry version + id hash + control checksum + survivorship hash + prompt/model | `pipeline/run.py`, `run_records/` |

Frame and control enter only at Layer 7. Nothing upstream reads them.

## Assumptions register

| Assumption | Defended by | Falsified if | 2026-09-09 build |
|---|---|---|---|
| The CBP frame is a floor | Contractor-coded plants (236220 · 238130 · 423390) counted separately; code set never widened | Contractor-coded plants exceed a few hundred, or bias changes materially with them included | holding; 4 known cases |
| The control is independent | G4 | Any row traces to the control; checksum moves without a logged triage edit | holding |
| The classifier can be trusted | G5 | Either threshold misses, including on the core-NAICS set | 100 / 96, n=60 |
| Facilities are distinct | G1 | Dup rate > 2% | 1.4% (5,096 → 5,026) |
| No two plants are merged into one | G2 | Any flagged cluster | built in v1.0; unmeasured on real data |
| IC-numbers are stable | G3 | Any renumbering | holding |
| A trading name is not an identity | Layer 4 | Resolution rate falls; a control row found already in the DB under another name | layer not built |
| An address is a plant, not an HQ | Tier model; T3 needs site-visit or rooftop evidence | A T3 facility is a mail drop | ~2,000 geocoded |
| "Active" is inferred except in TX | Status basis per row; survivorship prefers dated expiry | A row shows active with no basis | flagged per row |
| Sources are current | Vintage per pull; drift check | A source silently shrinks or its domain changes hands | manual today |
| Geography is unbiased | Bias band; out-of-band published with cause | A state leaves the band with no cause on record | MAB 0.30; OH · KY · MO out |
| The golden table is derived, never edited | Rebuilt every run from assertions + ids + rules; corrections are operator assertions | A golden value with no assertion behind it | enforced by construction |

## What makes an AI step regimented

Frozen prompt under version control; model id pinned and recorded; temperature 0; output
validated against a schema before it is accepted; batches keyed by content hash so a re-run is
a no-op; raw responses archived; a seeded eval gate on every batch. Under those conditions the
AI step is reproducible to within the eval tolerance, and the run record says exactly which
prompt and model produced every label.

## Cadence

Quarterly full run · annual frame refresh · between runs nothing merges.

## Build order

1. Source fetchers for the active class A/B/C sources (`pipeline/sources/<id>.py`).
2. Load the real control, seeds, frame totals and the frozen prompt from the 2026-09-09 build.
3. Layer 4 entity resolution — the binding constraint on recall.
4. Class D certification directories through Playwright — 700–1,100 plants in the weakest segments.
5. Compare a Cowork prototype build against a GitHub run with `pipeline/compare.py`.

## What was retired, and why

Next-source selection, the bias-steered queue, the four stopping rules and the 10-iteration
checkpoint. They were the right instruments for discovery — they fired correctly and
unprompted at iteration 8 — but a process whose inputs are fixed has no decision for them to
make. Their findings survive as the source registry's status column and the assumptions
register's evidence.
