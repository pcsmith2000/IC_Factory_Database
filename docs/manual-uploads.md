# Sources that must be uploaded by hand

Five active sources publish no file a fetcher can pull: the state either publishes nothing, or
blocks automation, or hides the list behind a search that needs a browser. `pipeline/sources/<id>.py`
still parses them — the file just has to be put in the store first. Until each one is there,
Layer 1 halts the whole run (`run.py` halts if *any* active source fails).

The store is the Vercel Blob store the pipeline already reads from — the same one holding
`pa_dced`, `tx_tdlr`, `iibc`, `mhi_plants` and `epa_frs`.

**Upload with the CLI, not the dashboard.** Files are keyed
`ic-sources/<source_id>/<YYYY-MM-DD>/<filename>`, and a file at the wrong key is invisible to the
run — which then reports the source as EMPTY, with nothing to say why. This builds the key and
the manifest for you:

    export BLOB_READ_WRITE_TOKEN=...          # the store's read-write token
    python -m pipeline.archive put or_bcd ~/Downloads/prefab-licences.csv
    python -m pipeline.archive put ma_bbrs ~/Downloads/certified-manufacturers.pdf

Pass several files at once if a source needs them, and `--date YYYY-MM-DD` to write a folder
other than today's. The run reads the **newest** date folder.

The extension matters — each parser branches on it, so the table below gives the formats that
parser actually accepts.

Check what the store holds at any time with:

    python -m pipeline.sources.refresh --list
    python -m pipeline.archive ls ic-sources/       # or any prefix: size, upload time, key

## When it was already uploaded somewhere else

A collection often arrives as a folder named after the collection — `additional_directories_
2026-09-18/` — rather than after any source id, because whoever made it was not thinking in source
ids. Two ways out, and neither needs the file re-uploaded from a laptop:

    # tell the registry where the bytes actually are (preferred: the registry stays the one
    # place that answers "where does this source's data come from")
    blob_path: ic-sources/additional_directories_2026-09-18/

    # or copy them into the convention, keeping the arrival where it was put
    python -m pipeline.archive adopt --from ic-sources/additional_directories_2026-09-18/ \
        --source-id ic_directories_more --date 2026-09-18 --dry-run

Both of those, and `ls`, run on a runner through `.github/workflows/blob-admin.yml` when you would
rather the store's token stayed in the repository's secrets. Note GitHub only offers a dispatch
for workflows that exist on the DEFAULT branch.

---

## or_bcd — Oregon BCD, prefabricated structures

| | |
|---|---|
| Upload to | `ic-sources/or_bcd/<date>/` |
| Accepts | `.csv`, `.txt`, `.xlsx` (preferred) or `.pdf` |
| Get it from | https://www.oregon.gov/bcd/licensing/pages/search.aspx |

Search the licence database with **programme: Prefabricated Structures**, active only, and export
the result. Registers per manufacturing location, which is the cleanest match to our unit of
record — worth getting right.

Do **not** upload https://www.oregon.gov/bcd/permit-services/prefab/Documents/prefab-registered-manufacturers.pdf.
That is the official "list" and it contains no data; I fetched it and it parses to zero rows. The
registry records this as a trap.

The CSV parser keeps only rows whose text contains `prefab`, so export with the programme column
included.

## fl_bcis — Florida BCIS, manufactured (modular) buildings

| | |
|---|---|
| Upload to | `ic-sources/fl_bcis/<date>/` |
| Accepts | `.html` (save the results page) |
| Get it from | https://floridabuilding.org/mb/mb_org_srch.aspx |

Set **Organization Type** to `(Select All)` and search, then save the results page as HTML. There
is no Manufacturer option in that dropdown — every organisation in the `/mb/` section is already
in the manufactured-buildings programme, so Select All is correct.

I can drive this page's WebForms postback, but BCIS answers with its "System Error" page rather
than results — it needs the ASP.NET session a real browser holds. The fetcher now refuses that
error page instead of parsing it into rows, so this stays a manual export.

Registry trap: ~852 rows are names only, with no address. They can never be promoted past T0 on
their own, and that is expected — do not filter them out of the export.

## ma_bbrs — Massachusetts BBRS, manufactured buildings

| | |
|---|---|
| Upload to | `ic-sources/ma_bbrs/<date>/` |
| Accepts | `.pdf` only |
| Get it from | https://www.mass.gov/info-details/manufactured-building-program |

mass.gov blocks automation, so fetch it in a browser. The parser reads the PDF text and splits
entries on `City, ST zip` lines, so the certified-manufacturers PDF is the right artifact.

Registry trap: the PDF is undated and still lists Kullman, Excel Homes and Modtech — defunct 10+
years. Status is inferred, not published. Upload it as-is anyway; the pipeline records the
staleness rather than correcting it.

## ny_dos — New York DOS, factory manufactured buildings

| | |
|---|---|
| Upload to | `ic-sources/ny_dos/<date>/` |
| Accepts | `.xlsx` or `.csv` (preferred) or `.pdf` |
| Get it from | https://dos.ny.gov/code/factory-manufactured-buildings-modular |

DOS publishes no manufacturer list on the programme page and blocks automation — the FOIL response
is the source. The registry notes a FOIL request was drafted; this is the one entry here that may
need a records request rather than a download.

Registry trap: approval-centric, not plant-centric — one row per approval, not per plant. The
parser labels the rows accordingly, so upload whatever shape DOS returns.

## mi_lara — Michigan LARA, premanufactured units

| | |
|---|---|
| Upload to | `ic-sources/mi_lara/<date>/` |
| Accepts | `.xlsx`, `.html` or `.pdf` |
| Get it from | https://www.michigan.gov/lara/bureau-list/bcc/sections/plan-review/premanufactured-units/premanufactured-units-program |

Registry trap: the list lives under **Plan Review**, not the Premanufactured Units programme page,
which is a dead end. If nothing is published there either, it has to come from the Bureau directly.

Keep source typos verbatim ("Shangahi") — Layer 2 expects them.

---

## After uploading

    python -m pipeline.archive verify             # proves the token and store, leaves nothing behind
    python -m pipeline.sources.refresh --list     # confirm each shows a date, not EMPTY

Then re-run. A source whose file is present but unparseable fails loudly at Layer 1 with the
parser's own message naming the file — that is the signal to check the format against the table
above, not a reason to re-upload blindly.
