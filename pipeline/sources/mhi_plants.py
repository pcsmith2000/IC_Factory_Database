"""mhi_plants — Manufactured Housing Institute HUD-code plant list (PDF).

MHI publishes a dated "Manufactured Home Plant List"; the file name carries the date, so the
registry `url` is the durable pointer and updating it is the versioned registry edit.

Verified 2026-09-15 against Plant-List-10_3_24.pdf (7 pages): a whitespace-aligned table
    MFG # | Plant Code | Manufacturer | CITY | STATE
Plant codes (TMOD01, CVLR09, ...) are durable per-facility identifiers and are captured in
source_identifier — the reason this source is worth pulling at all.

There is NO street address in this list. Every row is therefore name + city + state only, which
cannot carry a facility past T0 on its own; it reaches a street address only by matching another
source. That is recorded rather than worked around.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import (pick, http_get, pdf_lines, drop_repeated_lines, detect_columns, slice_columns,
                      contract_row, require)

URL = "https://www.manufacturedhousing.org/wp-content/uploads/2024/10/Plant-List-10_3_24.pdf"
LABELS = ["MFG", "Plant", "Manufacturer", "CITY", "STATE"]
ROW_NO = re.compile(r"^\d{1,4}$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    url = source.get("url") or URL
    return [http_get(url, archive_dir, url.rsplit("/", 1)[-1])]


def parse(paths: list[Path], source: dict) -> list[dict]:
    wanted = pick(paths, ".pdf")
    require(bool(wanted), paths[0], "no PDF among " + str([p.name for p in paths]))
    path = wanted[0]
    lines = pdf_lines(path)
    cols = detect_columns(lines, LABELS, path)
    out = []
    for line in drop_repeated_lines(lines):
        cells = {k: v.strip() for k, v in slice_columns(line, cols).items()}
        if not ROW_NO.match(cells["MFG"].split()[0] if cells["MFG"] else ""):
            continue          # not a numbered plant row
        name, state = cells["Manufacturer"], cells["STATE"]
        if not name or len(state) != 2:
            continue
        out.append(contract_row(source, int(cells["MFG"].split()[0]), name=name, city=cells["CITY"], state=state,
                                source_url=source.get("url") or URL, source_document=path.name,
                                source_identifier=cells["Plant"],
                                notes="MHI plant list: plant code is a durable facility id; no street address published"))
    require(bool(out), path, "columns resolved but no numbered plant rows parsed")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
