# The 19-column contract CSV

Every source, whatever its class or method, lands as one CSV with exactly these columns in this
order. Layer 2 validates against it; nothing downstream reads anything else.

Verbatim columns are copied from the source without correction — typos included. Normalised
columns are **added** by Layer 2, never substituted. Blank means blank.

| # | Column | Rule |
|---|---|---|
| 1 | `source_id` | id from `registry/sources.yaml` |
| 2 | `source_url` | URL or document the row came from |
| 3 | `source_document` | file name or page title |
| 4 | `retrieved_date` | ISO date of the pull |
| 5 | `row_position` | 1-based position in the source (provenance) |
| 6 | `name_verbatim` | company / plant name exactly as published |
| 7 | `address_verbatim` | street address exactly as published, or blank |
| 8 | `city_verbatim` | exactly as published |
| 9 | `state_verbatim` | exactly as published |
| 10 | `zip_verbatim` | exactly as published |
| 11 | `country` | `US` unless the source says otherwise |
| 12 | `source_identifier` | the source's own id for the row (plant code, mill number, licence number, FRS id) |
| 13 | `naics_verbatim` | if the source carries it |
| 14 | `status_verbatim` | status text as published |
| 15 | `status_basis` | `dated_expiry` · `on_current_list` · `explicit_status_field` · `certified_as_of_date` · `none` |
| 16 | `expiry_date` | ISO date, TX and any dated source; else blank |
| 17 | `lat` | if the source carries it (EPA FRS) |
| 18 | `lon` | as above |
| 19 | `notes` | extraction notes only — never inferred facts |

Columns 1–5 are non-null on every row (Layer 2 halts otherwise). Columns 6 and 9 are non-null
on every row that is a candidate facility. A row with no address is allowed (FL is names only)
but can never be promoted past T0 on its own.

## Columns added by Layer 2

`city_norm` · `street_key` · `state` · `no_fixed_plant` (bool) · `contract_version` · `row_hash`

`street_key` is the normalised street number + street name, the strongest dedupe signal.
`city_norm` collapses spacing, case, "St"/"Saint", township vs post town where a mapping exists.
`row_hash` is the SHA-256 of the verbatim columns — the idempotency key for re-runs.
