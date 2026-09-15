"""iibc — Interstate Industrialized Buildings Commission: registered manufacturing facilities.

https://interstateibc.org/manufacturers/ is one HTML table. Verified 2026-09-15:
    Name | Address | City | ST | 2023 | 2024 | 2025 | 2026
286 data rows; one column per calendar year, "R" meaning the facility was registered that year
(registrations expire 31 December). City and state are their own columns, so nothing has to be
split out of the address. There is no ZIP column and no per-facility page to link to.

The most recent year column that carries an R is the liveness signal, so status_basis is
certified_as_of_date; a row registered in no listed year keeps on_current_list and says so.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import http_get, pick, contract_row, require

PAGE = "https://interstateibc.org/manufacturers/"
YEAR = re.compile(r"^(19|20)\d{2}$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [http_get(source.get("url") or PAGE, archive_dir, "manufacturers.html")]


def parse(paths: list[Path], source: dict) -> list[dict]:
    from bs4 import BeautifulSoup
    wanted = pick(paths, ".html", ".htm")
    require(bool(wanted), paths[0], "no HTML among " + str([p.name for p in paths]))
    path = wanted[0]
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    table = header = None
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if not rows:
            continue
        cells = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
        low = [c.lower() for c in cells]
        if "name" in low and "address" in low:
            table, header = t, cells
            break
    require(table is not None, path, "no table with Name and Address columns on the IIBC page")
    low = [c.lower() for c in header]
    idx = {k: low.index(k) for k in ("name", "address", "city") if k in low}
    state_i = next((i for i, c in enumerate(low) if c in ("st", "state")), None)
    years = [(i, c) for i, c in enumerate(header) if YEAR.match(c)]
    require("name" in idx and state_i is not None, path, f"columns changed: {header}")

    out = []
    for i, tr in enumerate(table.find_all("tr")[1:], 1):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        get = lambda j: cells[j] if j is not None and j < len(cells) else ""
        name = get(idx["name"])
        if not name:
            continue
        reg = [y for j, y in years if get(j).upper().startswith("R")]
        out.append(contract_row(
            source, i, name=name, address=get(idx.get("address")), city=get(idx.get("city")), state=get(state_i),
            source_url=PAGE, source_document=path.name,
            status=f"registered {','.join(reg)}" if reg else "",
            status_basis="certified_as_of_date" if reg else "on_current_list",
            notes="" if reg else f"listed but registered in none of {','.join(y for _, y in years)}"))
    require(bool(out), path, "table found but no facility rows parsed")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
