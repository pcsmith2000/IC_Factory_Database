# Gathering corporate_locations by hand

`corporate_locations` is the only source in the pipeline that is not a government register. It
reads the "our locations" pages of large multi-site component manufacturers and takes the plant
addresses off them. Those pages are prose, not tables, which is why the source is flagged
`ai_extraction: true` and why it costs **one model call per archived page — 126 of them**.

That is the slowest and most failure-prone thing Layer 1 does, and a single rate-limit error
anywhere in the sequence fails the source and halts the whole run.

**So don't gather pages. Gather rows.** If the reading is already done, the run just loads it:
`parse()` prefers transcribed CSVs in the source's folder and skips extraction entirely — no
model calls, no structured-output dependency, no rate limits.

---

## What to produce

**One CSV per company**, named for the company: `builders-firstsource.csv`, `stark-truss.csv`,
`parr-truss.csv` and so on. Every CSV in the folder is read and the rows concatenated.

One file per company is the point. A company that redesigns its locations page is re-transcribed
and re-uploaded on its own; every other company keeps its rows, its row positions, and the file
name recorded against them. Each row carries the file it came from in `source_document` and the
page it came from in `source_url`, so a value in the warehouse can always be walked back to the
company, the file and the page. Row positions are numbered within each file, so revising one
company cannot shift the numbers recorded against another.

Each file has exactly these columns, header row included:

```
company,name,address,city,state,zip,kind,evidence,source_url
```

| column | required | notes |
|---|---|---|
| `company` | no | the parent, e.g. `Builders FirstSource`. For your own bookkeeping |
| `name` | **yes** | the plant as the page names it. Rows with a blank name are dropped |
| `address` | strongly | street address. Without it the facility can never rise above tier T0 |
| `city` | **yes** | |
| `state` | **yes** | two letters; case is normalised |
| `zip` | no | |
| `kind` | no | what the page calls it — `truss plant`, `manufacturing`, `distribution` |
| `evidence` | no | the phrase on the page that shows it is a plant. Goes into `notes` |
| `source_url` | strongly | the exact page the row came from — this is the row's provenance |

## The companies

| company | page |
|---|---|
| Builders FirstSource | https://www.bldr.com/location/all-locations |
| UFP Site Built | https://ufpsitebuilt.com/our-locations |
| 84 Lumber | https://www.84lumber.com/manufacturing/ |
| Stark Truss | https://www.starktruss.com/locations/ |
| The Truss Company | https://www.thetrussco.com/locations/ |
| Parr Truss | https://parr.com/locations/ |
| ABS | **no URL on record.** The registry notes "Automated Building Components" (abctruss.com) as the nearest match — confirm identity before adding rows, do not guess |

Builders FirstSource is the big one: ~570 locations, and the registry only wants the plant pages
(links matching `truss`, `manufactur`, `component`), not every branch.

## What counts as a row

Include a site only if **the page says it manufactures**. These companies mix plants with yards,
branches, showrooms and distribution centres, and the whole point of this source is the plants.

- **Include**: truss plants, component plants, panel plants, millwork/manufacturing facilities.
- **Exclude**: sales branches, lumber yards, showrooms, distribution-only sites, corporate offices.
- **Unsure**: include it and put what the page actually says in `kind` and `evidence`. An
  unlocated or ambiguous row is tiered down later; an invented one is a defect.

Transcribe verbatim. Keep the company's own spelling, punctuation and abbreviations, typos
included — Layer 2 expects the source's own text, and normalisation happens downstream.

Never infer an address the page does not state. A row with a name and city but no street is fine
and useful; a row with a street you reconstructed is not.

## Uploading

```bash
export BLOB_READ_WRITE_TOKEN=...
python -m pipeline.archive put corporate_locations *.csv        # all of them, one date folder
```

That writes each file to `ic-sources/corporate_locations/<today>/` with a manifest.

The run reads the **newest** date folder and every CSV in it, which is the one thing to be careful
about: a date folder is the complete set, not a patch. Re-uploading a single revised company into
a new date folder would leave the others behind. To update one company, put its new CSV alongside
copies of the current ones — `archive put` takes several files in one call, and
`python -m pipeline.archive list corporate_locations <date>` shows what a folder holds.

Confirm it landed:

```bash
python -m pipeline.sources.refresh --list     # corporate_locations should show a date
```

Then check it parses before trusting a run to it:

```bash
python -c "
from pathlib import Path; from pipeline.sources import corporate_locations as cl
from pipeline.registry import load_yaml
src = next(s for s in load_yaml(Path('registry/sources.yaml'))['sources'] if s['id']=='corporate_locations')
rows = cl.parse(sorted(Path('.').glob('*.csv')), src)
print(len(rows), 'rows')
import collections; print(collections.Counter(r['source_document'] for r in rows))"
```

A CSV that produces no rows, or is missing `name`/`city`/`state`, fails loudly at Layer 1 with the
file named — that is the signal to fix the CSV, not to re-upload the same thing.

## Coverage of the 2026-09-16 transcription, verified

126 pages were archived; the six CSVs carry 210 rows. Every archived page is accounted for:

| | pages | rows |
|---|---|---|
| builders-firstsource | 96 | 94 |
| ufp-site-built | 26 | 25 |
| 84-lumber | 1 | 54 |
| stark-truss | 1 | 15 |
| parr-truss | 1 | 14 |
| the-truss-company | 1 | 8 |

Four archived pages are cited by no row, and all four are correct omissions: three are the
`all-locations` / `our-locations` index pages, which are directories rather than plants, and the
fourth is `location-arizona-truss-design-ewp-multifamily-phoetrad`, which is a suite-number sales
office — *"Our Mesa, AZ office supports customers and business partners"* — with no manufacturing
claim on the page. That is the exclusion rule above working as intended.

For 84 Lumber and Parr Truss the CSVs go **beyond** the archive: only the index page was ever
fetched, and the rows cite per-location URLs that were never archived. Those 68 rows have no page
snapshot behind them and never did; their provenance is the `source_url` on each row.

## What this changes

The folder currently holds 126 archived HTML pages from an automated fetch. Transcribed CSVs take
precedence over them; the pages stay as provenance and cost nothing. Rows loaded this way
are marked `transcribed from the company page, not model-extracted` in `notes`, so the warehouse
can always tell them apart from anything a fetcher parsed itself.

With a CSV in place this source no longer needs `IC_AI=on` to work, which removes it as a blocker
on the first classified run. Layer 1 checks the archive for transcribed CSVs before deciding an
`ai_extraction` source needs a model, so a deterministic run picks them up too.

**Upload to `ic-sources/corporate_locations/<date>/`.** The 2026-09-16 transcription first landed
under an `ic-csv/` prefix, which nothing reads — `ic-csv/` is a *local* directory in the repo, not
a store prefix, so the files were inert and the source silently contributed nothing. Use
`python -m pipeline.archive put`, which builds the key for you, rather than the dashboard.
