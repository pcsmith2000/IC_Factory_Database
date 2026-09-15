"""iibc — Interstate Industrialized Buildings Commission: registered manufacturing facilities.

https://interstateibc.org/manufacturers/ is an HTML table: Facility · Address · one column per
calendar year, "R" = registered that year (registrations expire 31 December). Each facility
also has its own page (/manufacturers/<slug>/) which is recorded as the row's source_url when
linked. Registry trap: ~258 rows dated Oct 2024, not the ~700 a scout claimed — the parser
records the count and Layer 2 drift-checks it.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import http_get, contract_row, split_city_state_zip, require

PAGE = "https://interstateibc.org/manufacturers/"


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [http_get(source.get("url") or PAGE, archive_dir, "manufacturers.html")]


def parse(paths: list[Path], source: dict) -> list[dict]:
    from bs4 import BeautifulSoup
    path = paths[0]
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    table = next((t for t in soup.find_all("table") if re.search(r"facility|manufacturer", t.get_text(" ", strip=True)[:400], re.I)), None)
    require(table is not None, path, "no Facility/Address table on the IIBC manufacturers page")
    rows = table.find_all("tr")
    hdr = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    fac_i = next((i for i, h in enumerate(hdr) if re.search(r"facility|manufacturer|name", h, re.I)), 0)
    addr_i = next((i for i, h in enumerate(hdr) if re.search(r"address|location", h, re.I)), None)
    years = [(i, h) for i, h in enumerate(hdr) if re.fullmatch(r"(19|20)\d{2}", h.strip())]
    out = []
    for i, tr in enumerate(rows[1:], 1):
        cells = tr.find_all(["td", "th"])
        if not cells or not cells[fac_i].get_text(strip=True):
            continue
        name = cells[fac_i].get_text(" ", strip=True)
        link = cells[fac_i].find("a")
        url = link["href"] if link and link.get("href", "").startswith("http") else PAGE
        addr = city = state = zip_ = ""
        if addr_i is not None and addr_i < len(cells):
            lines = [s.strip() for s in cells[addr_i].get_text("\n", strip=True).split("\n") if s.strip()]
            if lines:
                city, state, zip_ = split_city_state_zip(lines[-1])
                addr = " ".join(lines[:-1]) if len(lines) > 1 else ""
                if not state:  # single-line "street, City, ST zip"
                    parts = lines[-1].rsplit(",", 2)
                    if len(parts) == 3:
                        addr, city, (state, zip_) = parts[0].strip(), parts[1].strip(), (split_city_state_zip(parts[1].strip() + ", " + parts[2].strip())[1:])
        reg_years = [h for j, h in years if j < len(cells) and cells[j].get_text(strip=True).upper().startswith("R")]
        out.append(contract_row(source, i, name=name, address=addr, city=city, state=state, zip_code=zip_, source_url=url,
                                source_document="manufacturers.html", status=("registered " + ",".join(reg_years)) if reg_years else "",
                                status_basis="certified_as_of_date" if reg_years else "on_current_list",
                                notes=f"registered years: {','.join(reg_years)}" if reg_years else ""))
    require(bool(out), path, "table parsed but no facility rows")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
