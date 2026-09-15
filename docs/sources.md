# Sources — fetcher status

One fetcher per active source under `pipeline/sources/<id>.py`, each `fetch()` → archived raw
file(s) → `parse()` → contract rows. Written against endpoints found by search on 2026-09-15;
**none has been run against the live site yet** (the build environment cannot reach them).
The first live run of each is the check:

```
pip install -e ".[acquire]"
python -m pipeline.sources.check tx_tdlr             # fetch + parse + validate → ic-csv/tx_tdlr.csv
python -m pipeline.sources.check tx_tdlr --file ~/Downloads/2-Certified_Manufacturers_List.pdf   # parse only
```

After each pull the raw files are uploaded to Vercel Blob when `BLOB_READ_WRITE_TOKEN` is set
(`pipeline/archive.py`); the EPA zip is recorded by hash and its filtered slice archived instead.
A fetcher that meets a layout it was not written for raises `LayoutChanged` naming the
archived file; one that needs a scripted browser raises `NeedsBrowser`. Both are recorded per
source in the run record and halt Layer 1. Nothing is skipped silently and nothing is guessed.

| id | what is fetched | how sure | what to check on the first live run |
|---|---|---|---|
| `pa_dced` | DCED landing page → XLSX via download manager | endpoint confirmed; XLSX columns matched by name | column headers; that non-PA plants come through with their own state |
| `tx_tdlr` | three PDFs (certified all-states, certified TX, registered) | endpoints confirmed; text-line grouping on `City, ST zip` | that pdfplumber keeps one entry per line block; registration-number pattern; expiry-date presence |
| `mi_lara` | Plan Review programme page → "Approved Manufacturers" link (PDF/XLSX/in-page table) | page confirmed; list link discovered per run | that the link text says "Approved Manufacturers"; format |
| `ma_bbrs` | programme page → certified-manufacturers document (PDF) | page confirmed; document link discovered per run | link text; PDF line layout |
| `or_bcd` | licence-search page → registration data file; fallback: programme PDF | both URLs confirmed; registry says the PDF is empty | whether the data file link exists and which column names the licence type; else Playwright sweep |
| `fl_bcis` | MB menu → organisation search → WebForms POST → results table | POST-only confirmed; form fields discovered per run | the search link text, the submit-button name, results table shape. Names only: rows stay T0 |
| `ny_dos` | programme page → manufacturer list if DOS ever publishes one; else halts naming the FOIL | no public list found | parse the FOIL response with `--file` |
| `epa_frs` | national_combined.zip (~730 MB) streamed: NAICS filter → OSHA-OIS flag → facility rows | URL and file names confirmed from EPA docs; column names from FRS metadata | column names on the first pull; runtime (three passes over 5.3M rows) |
| `iibc` | manufacturers page HTML table: Facility · Address · year columns | page and columns confirmed | whether Address is one cell with `<br>` (handled) or split cells |
| `mhi_plants` | MHI plant-list PDF (dated file name in the registry `url`) | URL confirmed; layout unknown → tables first, then text lines | plant-code pattern; update `url` when MHI posts a new list |
| `corporate_locations` | registry `pages:` (7 companies) → archived HTML → AI extraction → verbatim check | company pages found by search; ABS has no url on record | which pages are JS-rendered (text < 200 chars is reported, not extracted); extraction audit in `.cache/ic-sources/corporate_locations/<date>/extraction_audit.json` |

## AI extraction (corporate pages)
`pipeline/sources/_extract.py`: frozen prompt `prompts/EXTRACTION-PROMPT.md` (hash recorded),
model and temperature from `classifier` in `registry/config.yaml`, JSON-schema output, raw
response archived beside the page and cached by content hash. Every name / address / city
returned is checked against the archived page text; a value not on the page is dropped and
counted. Needs `ANTHROPIC_API_KEY`.

## Secrets and access the sources need
| need | used by |
|---|---|
| none — public GET | pa_dced, tx_tdlr, mi_lara, ma_bbrs, or_bcd (data file / PDF), iibc, mhi_plants, epa_frs |
| WebForms POST (no login) | fl_bcis |
| `ANTHROPIC_API_KEY` | corporate_locations (extraction), Layer 3 classification |
| `CENSUS_API_KEY` | Layer 7 frame refresh only (not a Layer 1 source) |
| `BLOB_READ_WRITE_TOKEN` | raw-file archive to Vercel Blob after every pull (optional locally; the run record says when it is off) |
| Playwright (`pip install -e ".[acquire]"` + `playwright install chromium`) | or_bcd if the data file route fails |
| FOIL response file | ny_dos |
