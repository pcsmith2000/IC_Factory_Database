"""tx_tdlr — Texas TDLR Industrialized Housing & Buildings: certified + registered manufacturers.

Three PDFs, all pulled (registry trap: certified and registered are different populations):
  2-Certified_Manufacturers_List.pdf   certified, all states
  3-Certified_Manufacturers_TX.pdf     certified, Texas plants (subset of the above — de-duplicated by registration no.)
  4-Manufacturers_List.pdf             registered but not approved to build
Index: https://www.tdlr.texas.gov/ihb/ihblists.htm. TX is the only class A source with expiry dates
(status_basis dated_expiry); the parser looks for a date on each entry and leaves it blank when absent.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import http_get, pdf_pages_text, contract_row, split_city_state_zip, iso_date, require

INDEX = "https://www.tdlr.texas.gov/ihb/ihblists.htm"
FILES = {
    "2-Certified_Manufacturers_List.pdf": "certified",
    "3-Certified_Manufacturers_TX.pdf": "certified (TX plant)",
    "4-Manufacturers_List.pdf": "registered",
}
BASE = "https://www.tdlr.texas.gov/ihb/pdf/"
DATE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
REG = re.compile(r"\b(?:Reg(?:istration)?\.?\s*(?:No\.?|#)?\s*)?(IHB-?\d{3,6}|\d{5,7})\b")
CSZ = re.compile(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [http_get(BASE + f, archive_dir, f) for f in FILES]


def _entries(lines: list[str]) -> list[list[str]]:
    """Group lines into entries: an entry ends on a 'City, ST 12345' line. Header/footer lines are dropped."""
    entries, cur = [], []
    for ln in lines:
        s = ln.strip()
        if not s or re.match(r"^(page \d+|texas department|industrialized|certified|registered|manufacturers?\b.*list|updated|as of)", s, re.I):
            continue
        cur.append(s)
        if CSZ.match(s) or re.search(r",\s*[A-Z]{2}\s+\d{5}", s):
            entries.append(cur); cur = []
    return entries


def parse(paths: list[Path], source: dict) -> list[dict]:
    out, seen = [], set()
    for path in paths:
        status = FILES.get(path.name, path.stem)
        text = "\n".join(pdf_pages_text(path))
        entries = _entries(text.splitlines())
        require(bool(entries), path, "no 'City, ST zip' lines found — PDF layout changed")
        for i, e in enumerate(entries, 1):
            name = e[0]
            csz = e[-1]
            m = CSZ.match(csz) or CSZ.match(re.sub(r"^.*?(?=[A-Za-z .'-]+,?\s+[A-Z]{2}\s+\d{5})", "", csz))
            city, state, zip_ = (m.group(1), m.group(2), m.group(3)) if m else split_city_state_zip(csz)
            middle = e[1:-1]
            addr = next((l for l in middle if re.match(r"^\d+\s", l) or re.match(r"^p\.?o\.? box", l, re.I)), middle[0] if middle else "")
            blob = " ".join(e)
            exp = next((iso_date(d) for d in DATE.findall(blob) if iso_date(d)), "")
            reg = next((r for r in REG.findall(blob) if r and not r.isdigit() or (r.isdigit() and 5 <= len(r) <= 7)), "")
            key = (name.lower(), addr.lower(), city.lower())
            if key in seen:
                continue  # the TX-only list repeats rows of the all-states list
            seen.add(key)
            out.append(contract_row(source, i, name=name, address=addr, city=city, state=state, zip_code=zip_,
                                    source_url=BASE + path.name, source_document=path.name, source_identifier=reg,
                                    status=status, status_basis="dated_expiry" if exp else "on_current_list",
                                    expiry_date=exp, notes="" if len(middle) <= 1 else "extra lines: " + " | ".join(middle[1:])[:200]))
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
