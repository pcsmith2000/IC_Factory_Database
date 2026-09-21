# Rebuilding this database from zero

Everything needed is preserved. The question this document answers is what ORDER to put it in,
because the answer is not obvious and getting it wrong fails quietly rather than loudly.

## What is preserved, and what that means

| held | where | why it matters |
|---|---|---|
| every source file ever read | Vercel Blob, `ic-sources/<source>/<date>/` | `archive.mode: blob-only` — the run never scrapes, so inputs cannot drift |
| every model call | `cache_lookup`, kind `locate` | a rebuild spends nothing on the classifier or the AI locate stage |
| every Geocodio answer | `cache_lookup`, kind `geocode` | including the ones gate E2 refused — stage 14 reads them |
| every Overture measurement | `cache_lookup`, kind `footprint` | pinned to an Overture release, so a new release correctly re-measures |
| every assertion ever made | `fact_assertions` | append-only, tagged by release |
| every layers 1-8 run | `run_records/`, `RUNLOG.csv` | one row per run, with its gates and its measurements |

**A from-zero rebuild therefore costs S3 reads and runner minutes, and no provider spend at all**
— as long as the warehouse's `cache_lookup` table survives. If it does not, the rebuild is
correct but expensive: roughly 2,100 Geocodio lookups and 1,280 model calls to re-earn.

## The order

```
1.  python -m pipeline.warehouse init          # schema, idempotent
2.  run.yml            layers 1-8, ai: on      # sources -> classify -> reconcile -> golden
3.  enrich.yml         stages: plan,locate,geocode,places,anchor,footprint,existence,load,promote
                       target: main
```

Step 3 is one dispatch, not several. The stage order inside it is not arbitrary:

- `geocode` before `places` — a rooftop answer, where there is one, should be taken first.
- `places` before `anchor` — both can supply a coordinate; the place match is the better of the two.
- `anchor` before `footprint` — a coordinate confirmed this run should be available to be measured.
- `load` before `promote` — the gates run against what is about to be written, not after it.

`places` bounds itself to `--places-limit` states per run and `anchor` to `--anchor-limit` Overture
files, so **step 3 repeats until `deferred` reaches 0 in both**. Around six passes covers the
country. Nothing is lost by repeating it: every stage is idempotent on `row_hash`.

## The two things that made this non-obvious

**The release tag.** `promote` reads `fact_assertions` by release tag, and the tag embeds hashes of
`registry/survivorship.yaml` and the registry. Editing either publishes under a new tag, and for a
while that stranded every enrichment assertion under the old one — 3,223 assertions across 2,006
facilities, including 1,408 paid-for Geocodio lookups. `fetch_assertions` now carries enrichment
forward for any facility the current release still asserts something about, so the durable layer
survives a release while a facility the release DROPPED stays dropped.

**Ledger markers must carry the tag too.** Stage 13 records which states it has read so a second
run does not re-read the same six. That marker means "the assertions for this state are in the
database" — which stops being true the moment a new tag is published. Keyed on the Overture
release alone, a rebuild would find all 52 states marked done, write nothing, and report success
with ~2,000 websites missing. The key now carries the release tag as well.

The general form of both: **a cache key must name everything the cached answer depends on.** A
footprint depends on a coordinate and an Overture release. A state marker depends on an Overture
release and a release tag. Anything left out of the key is a silent wrong answer later.

## Checking a rebuild landed

`promote` prints `coverage_before` and `coverage_after` per field, and gate E6 fails the stage if
a rebuild leaves fewer facilities carrying a field than before. Against the release of
2026-09-21 the figures to expect are:

| field | facilities |
|---|---|
| name | 6,531 |
| address | 5,542 |
| lat_lon | 4,840 |
| phone | 3,225 |
| website | 2,016 |
| building_sqft | 3,281 |

A rebuild that lands materially under those has skipped something — the first thing to check is
`states_already_read` in the places stage and `deferred` in both bounded stages.
