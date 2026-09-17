"""mi_lara — Michigan LARA Bureau of Construction Codes, premanufactured units: approved manufacturers.

Registry trap: the list lives under Plan Review, not the Premanufactured Units program page.
The program page under Plan Review is fetched and the "Approved Manufacturers" link discovered
on it (search results confirm LARA publishes "Approved Manufacturers" and "Approved Inspection
Agencies" lists there; the file name and format are not fixed, so they are discovered per run).
PDF or XLSX are both handled. Typos verbatim ("Shangahi").
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import NeedsBrowser, http_get, pdf_pages_text, xlsx_rows, html_tables, contract_row, split_city_state_zip, require

PAGE = "https://www.michigan.gov/lara/bureau-list/bcc/sections/plan-review/premanufactured-units/premanufactured-units-program"
CSZ = re.compile(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    page = http_get(source.get("url") or PAGE, archive_dir, "program.html")
    html = page.read_text(encoding="utf-8", errors="replace")
    cands = [(t, h) for h, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S)
             if re.search(r"approved\s+manufacturer", re.sub("<[^>]+>", " ", t), re.I) or re.search(r"approved.?manufacturer", h, re.I)]
    if not cands:
        # the list may be an in-page table rather than a file
        if any(re.search(r"approved manufacturers", "".join(sum(t, [])), re.I) for t in html_tables(html)):
            return [page]
    if not cands:
        raise NeedsBrowser("""mi_lara: VERIFIED 2026-09-15 — Michigan publishes no approved-manufacturers list. The Plan Review
premanufactured-units programme page and the Compliance Assurance Program page carry only forms,
fee schedules and the Accela portal link. The list is not public; request it from the Bureau
(bccpermits@michigan.gov, 517-241-9313) and parse the reply with:
    python -m pipeline.sources.check mi_lara --file <file>""")
    href = cands[0][1]
    url = href if href.startswith("http") else "https://www.michigan.gov" + href
    ext = ".xlsx" if re.search(r"\.xlsx?(\?|$)", url, re.I) else ".pdf"
    return [http_get(url, archive_dir, "approved_manufacturers" + ext)]


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[0]
    out = []
    if path.suffix == ".xlsx":
        for i, r in enumerate(xlsx_rows(path), 1):
            name = next((v for k, v in r.items() if "name" in k.lower() or "manufacturer" in k.lower()), "")
            if not name: continue
            get = lambda *ks: next((v for k, v in r.items() if any(x in k.lower() for x in ks)), "")
            out.append(contract_row(source, i, name=name, address=get("address", "street"), city=get("city"), state=get("state"),
                                    zip_code=get("zip"), source_url=PAGE, source_document=path.name, source_identifier=get("number", "id", "certificate")))
    elif path.suffix == ".html":
        table = next(t for t in html_tables(path.read_text(errors="replace")) if any(re.search("manufacturer", c, re.I) for c in t[0]))
        hdr = [c.lower() for c in table[0]]
        for i, cells in enumerate(table[1:], 1):
            r = dict(zip(hdr, cells))
            get = lambda *ks: next((v for k, v in r.items() if any(x in k for x in ks)), "")
            out.append(contract_row(source, i, name=get("manufacturer", "name"), address=get("address"), city=get("city"), state=get("state"),
                                    zip_code=get("zip"), source_url=PAGE, source_document="program.html"))
    else:
        entries, cur = [], []
        for ln in "\n".join(pdf_pages_text(path)).splitlines():
            s = ln.strip()
            if not s: continue
            cur.append(s)
            if CSZ.match(s):
                entries.append(cur); cur = []
        for i, e in enumerate(entries, 1):
            m = CSZ.match(e[-1]); city, state, zip_ = m.group(1), m.group(2), m.group(3)
            addr = next((l for l in e[1:-1] if re.match(r"^\d+\s", l)), e[1] if len(e) > 2 else "")
            out.append(contract_row(source, i, name=e[0], address=addr, city=city, state=state, zip_code=zip_,
                                    source_url=PAGE, source_document=path.name))
    require(bool(out), path, "no manufacturer rows parsed")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
