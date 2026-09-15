"""pa_dced — Pennsylvania DCED industrialized housing: approved manufacturers list.

Landing page (durable bookmark): https://dced.pa.gov/housing-and-development/website-list-of-manufacturers/
The page calls it "an Excel file" but serves a CSV from a WordPress theme path
(/wp-content/themes/.../csv/manufacturers_list.csv), which is exactly the trap the registry
records. The link is therefore discovered from the page each run — CSV, XLSX or the download
manager (?wpdmdl=) are all accepted — rather than hard-coded.

Traps (registry): ~36 of ~112 rows are PA plants, the rest are out-of-state and Canadian plants
approved to ship in; source typos are kept verbatim.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import http_get, xlsx_rows, csv_rows, contract_row, split_city_state_zip, require, LayoutChanged

LANDING = "https://dced.pa.gov/housing-and-development/website-list-of-manufacturers/"


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    page = http_get(source.get("url") or LANDING, archive_dir, "landing.html")
    html = page.read_text(encoding="utf-8", errors="replace")
    links = [h for h, _ in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S)]
    cands = [l for l in links if re.search(r"manufactur[a-z_]*\.(csv|xlsx?)(\?|$)", l, re.I)] \
        or [l for l in links if re.search(r"\.(csv|xlsx?)(\?|$)", l, re.I) and re.search(r"manufactur", l, re.I)] \
        or [l for l in links if "wpdmdl" in l and re.search(r"manufactur", l, re.I)]
    require(bool(cands), page, "no manufacturers CSV/XLSX link on the DCED landing page")
    url = cands[0] if cands[0].startswith("http") else "https://dced.pa.gov" + cands[0]
    ext = (re.search(r"\.(csv|xlsx?)(\?|$)", url, re.I) or [None, "csv"])[1].lower()
    return [http_get(url, archive_dir, f"manufacturers.{ext}")]


def _pick(row: dict, *names: str) -> str:
    for k, v in row.items():
        kl = k.lower()
        if any(n in kl for n in names):
            return v or ""
    return ""


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[0]
    rows = csv_rows(path) if path.suffix.lower() == ".csv" else xlsx_rows(path)
    require(bool(rows), path, "XLSX has no data rows")
    out = []
    for i, r in enumerate(rows, 1):
        name = _pick(r, "manufacturer", "company", "name")
        if not name.strip():
            continue
        addr = _pick(r, "address", "street")
        city, state, zip_ = _pick(r, "city"), _pick(r, "state"), _pick(r, "zip")
        if not city and not state:
            city, state, zip_ = split_city_state_zip(_pick(r, "city/state", "location"))
        out.append(contract_row(source, i, name=name, address=addr, city=city, state=state, zip_code=zip_,
                                source_url=LANDING, source_document=path.name,
                                source_identifier=_pick(r, "approval", "certificate", "number"),
                                status=_pick(r, "approval type", "residential", "commercial", "type"),
                                notes="; ".join(f"{k}={v}" for k, v in r.items() if v and any(t in k.lower() for t in ("evaluation", "inspection", "agency")))[:300]))
    require(bool(out), path, "no rows with a manufacturer name — column headers changed?")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
