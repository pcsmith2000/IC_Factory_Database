"""tx_tdlr — Texas TDLR Industrialized Housing & Buildings: certified + registered manufacturers.

Three PDFs, all pulled (registry trap: certified and registered are different populations):
  2-Certified_Manufacturers_List.pdf   certified, all states
  3-Certified_Manufacturers_TX.pdf     certified, Texas plants (a subset — de-duplicated by Reg #)
  4-Manufacturers_List.pdf             registered but not approved to build
Index: https://www.tdlr.texas.gov/ihb/ihblists.htm

Layout (verified against the 2026-09-15 files): a whitespace-aligned table, one record per two or
more lines, columns Reg # · Name · Exp Date · Physical Address · Mailing Address · Phone · DRA · TPIA.
The three files share that structure at different x positions, so columns are calibrated per file
(_common.detect_columns) rather than hard-coded. Only the physical address is used as the plant
address — the mailing column is full of PO boxes — and it is kept in `notes`.

TX is the only class A source with expiry dates, so status_basis is dated_expiry.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import (pick, http_get, pdf_lines, drop_repeated_lines, detect_columns, slice_columns,
                      contract_row, split_address, iso_date, require)

INDEX = "https://www.tdlr.texas.gov/ihb/ihblists.htm"
BASE = "https://www.tdlr.texas.gov/ihb/pdf/"
FILES = {
    "2-Certified_Manufacturers_List.pdf": "certified",
    "3-Certified_Manufacturers_TX.pdf": "certified (TX plant)",
    "4-Manufacturers_List.pdf": "registered",
}
# "Name" is not asked for as a column: it starts only a few points after the Reg # ends, too close
# for a gap-based column detector to separate. The Reg # column therefore carries "IHM-234 ACME INC"
# and the registration number is split off the front by pattern, which works at any x position.
LABELS = ["Reg", "Exp", "Physical", "Mailing", "Phone"]
REG = re.compile(r"^[A-Z]{2,4}-?\d{2,6}$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [http_get(BASE + f, archive_dir, f) for f in FILES]


def _records(lines: list[list[dict]], cols) -> list[dict]:
    """A record starts on a line whose Reg # column holds a registration number; the lines that
    follow with no Reg # are its wrapped address lines and are appended column by column."""
    records: list[dict] = []
    for line in lines:
        cells = {k: v.strip() for k, v in slice_columns(line, cols).items()}
        head = cells["Reg"]
        first = head.split()[0] if head else ""
        if REG.match(first):
            cells["Reg"], cells["Name"] = first, head[len(first):].strip()
            records.append(cells)
        elif records and any(cells.values()):
            for k, v in cells.items():
                if v:
                    key = "Name" if k == "Reg" else k     # a wrapped name continues under the Reg column
                    records[-1][key] = (records[-1].get(key, "") + " " + v).strip()
    return records


def parse(paths: list[Path], source: dict) -> list[dict]:
    wanted = pick(paths, ".pdf")
    require(bool(wanted), paths[0], "no PDF among " + str([p.name for p in paths]))
    out, seen = [], set()
    for path in wanted:
        status = FILES.get(path.name, path.stem)
        lines = pdf_lines(path)
        cols = detect_columns(lines, LABELS, path)
        records = _records(drop_repeated_lines(lines), cols)   # page headers/footers off first
        require(bool(records), path, "columns resolved but no rows carried a registration number")
        for i, r in enumerate(records, 1):
            street, city, state, zip_, country = split_address(r.get("Physical", ""))
            if not r.get("Name"):
                continue
            key = (r["Reg"], r["Name"].lower())
            if key in seen:      # the TX-only list repeats rows of the all-states list
                continue
            seen.add(key)
            exp = iso_date(r.get("Exp", "").strip())
            out.append(contract_row(source, i, name=r["Name"], address=street, city=city, state=state, zip_code=zip_,
                                    country=country, source_url=BASE + path.name, source_document=path.name,
                                    source_identifier=r["Reg"], status=status,
                                    status_basis="dated_expiry" if exp else "on_current_list", expiry_date=exp,
                                    notes=f"mailing: {r['Mailing']}" if r.get("Mailing") else ""))
    require(bool(out), paths[0], "no rows parsed from any of the three TDLR PDFs")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
