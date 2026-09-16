# Gathering corporate_locations by hand

`corporate_locations` is the only source in the pipeline that is not a government register. It
reads the "our locations" pages of large multi-site component manufacturers and takes the plant
addresses off them. Those pages are prose, not tables, which is why the source is flagged
`ai_extraction: true` and why it costs **one model call per archived page — 126 of them**.

That is the slowest and most failure-prone thing Layer 1 does, and a single rate-limit error
anywhere in the sequence fails the source and halts the whole run.

**So don't gather pages. Gather rows.** If the reading is already done, the run just loads it:
`parse()` prefers a `locations.csv` in the source's folder and skips extraction entirely — no
model calls, no structured-output dependency, no rate limits.

---

## What to produce

One CSV, exactly these columns, header row included:

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
python -m pipeline.archive put corporate_locations locations.csv
```

That writes `ic-sources/corporate_locations/<today>/locations.csv` and the manifest. The run reads
the **newest** date folder, so a later, better CSV supersedes an earlier one without deleting
anything.

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
rows = cl.parse([Path('locations.csv')], src)
print(len(rows), 'rows'); print(rows[0])"
```

A CSV that produces no rows, or is missing `name`/`city`/`state`, fails loudly at Layer 1 with the
file named — that is the signal to fix the CSV, not to re-upload the same thing.

## What this changes

The folder currently holds 126 archived HTML pages from an automated fetch. A `locations.csv`
takes precedence over them; the pages stay as provenance and cost nothing. Rows loaded this way
are marked `transcribed from the company page, not model-extracted` in `notes`, so the warehouse
can always tell them apart from anything a fetcher parsed itself.

With a CSV in place this source no longer needs `IC_AI=on` to work, which removes it as a blocker
on the first classified run.
