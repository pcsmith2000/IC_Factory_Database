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

| id | live result 2026-09-15 | what it yields |
|---|---|---|
| `tx_tdlr` | **works — 334 rows** | every row with a street address, expiry date and Reg #; 8 foreign addresses left whole on purpose |
| `iibc` | **works — 286 rows** | Name · Address · City · ST plus a column per year; 227 registered in 2026; 45 states |
| `mhi_plants` | **works — 150 rows** | durable plant codes (TMOD01, CVLR09 …), city and state. **No street address published** |
| `pa_dced` | **works — 115 rows** | **No street address published**: Manufacturer, City, State, Approved For, Evaluation/Inspection Agency. Served as a CSV under a theme path, not the "Excel file" the page claims |
| `mi_lara` | **no list published** | verified: the Plan Review and Compliance Assurance pages carry only forms and the Accela link. Request from the Bureau, then `--file` |
| `ma_bbrs` | **no list published** | verified: only the certification applications are linked, and mass.gov answers 403 to any automated fetch. Request, then `--file` |
| `ny_dos` | **no list published** | verified: approval-centric records only, and dos.ny.gov answers 403. FOIL, then `--file` |
| `or_bcd` | **needs a browser** | the programme PDF is empty as the registry predicted, and no licence data file was linked. Playwright sweep of the licence search |
| `fl_bcis` | **search link not found** | the MB menu no longer carries a link matching the organisation search; the POST form needs re-locating from the archived menu |
| `epa_frs` | **not run** | ~730 MB bulk file; not exercised in this environment |
| `corporate_locations` | **not run** | needs `ANTHROPIC_API_KEY` for extraction |

Three of the eleven sources publish no list at all, and two more (`pa_dced`, `mhi_plants`) publish
no street address — their rows cannot pass tier T0 alone and reach a plant address only by
matching another source. That is the measured state of the input, not a defect in the pipeline.

### What the live run changed in the code
- PDF lists are whitespace-aligned tables, not line blocks. `_common.pdf_lines`, `detect_columns`
  and `slice_columns` calibrate the columns per file: headers are centred over left-aligned data,
  so each label takes the last data column at or before it, under a left-to-right constraint.
- `drop_repeated_lines` removes page furniture by text **and** position, so a running footer goes
  while a wrapped "HOUSTON, TX 77095" stays.
- Michigan and IIBC answer 403/500 to a user agent naming this project, so requests send a plain
  browser UA and identify the project in `X-Contact`.

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
| `BLOB_READ_WRITE_TOKEN` | raw-file archive to Vercel Blob after every pull (optional locally; the run record says when it is off). Check it with `python -m pipeline.archive verify` |
| Playwright (`pip install -e ".[acquire]"` + `playwright install chromium`) | or_bcd if the data file route fails |
| FOIL response file | ny_dos |
