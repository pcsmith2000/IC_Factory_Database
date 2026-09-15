"""ny_dos — New York DOS Division of Building Standards and Codes: factory manufactured buildings.

Registry trap: approval-centric, not plant-centric; extraction not clean; a FOIL request is
drafted. No public manufacturer list was located on dos.ny.gov (only forms, guidelines and the
insignia order form). The fetcher therefore:
  1. fetches the programme page and looks for any document whose link text names approved
     manufacturers or plants — if DOS publishes one, it is archived and parsed as a PDF;
  2. otherwise halts with NeedsBrowser naming the FOIL dependency, so Layer 1 reports
     ny_dos as not pulled instead of pretending.
A FOIL response received as a file is parsed with:  python -m pipeline.sources.check ny_dos --file <path>
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import NeedsBrowser, http_get, pdf_pages_text, xlsx_rows, csv_rows, contract_row, NeedsBrowser, require

PAGE = "https://dos.ny.gov/code/factory-manufactured-buildings-modular"
CSZ = re.compile(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    try:
        page = http_get(source.get("url") or PAGE, archive_dir, "program.html")
    except Exception as e:
        raise NeedsBrowser(
            "ny_dos: dos.ny.gov answers 403 to any automated fetch (verified 2026-09-15), and DOS publishes no "
            "manufacturer list — the records are approval-centric and a FOIL request is the route. "
            f"({type(e).__name__}: {e}). Obtain the list out of band and parse it with: "
            "python -m pipeline.sources.check ny_dos --file <file>") from e
    html = page.read_text(encoding="utf-8", errors="replace")
    cands = [h for h, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S)
             if re.search(r"(approved|certified|registered).{0,40}(manufacturer|plant)", re.sub("<[^>]+>", " ", t), re.I)]
    if not cands:
        raise NeedsBrowser("ny_dos: DOS publishes no manufacturer list on the programme page; the FOIL response is the source. "
                           "Parse it with: python -m pipeline.sources.check ny_dos --file <foil-file>")
    url = cands[0] if cands[0].startswith("http") else "https://dos.ny.gov" + cands[0]
    ext = (re.search(r"\.(pdf|xlsx?|csv)(\?|$)", url, re.I) or re.search(r"(pdf)", "pdf")).group(1).lower()
    return [http_get(url, archive_dir, "approved_manufacturers." + ext)]


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[0]
    out = []
    if path.suffix in (".xlsx", ".csv"):
        rows = xlsx_rows(path) if path.suffix == ".xlsx" else csv_rows(path)
        for i, r in enumerate(rows, 1):
            get = lambda *ks: next((v for k, v in r.items() if any(x in k.lower() for x in ks)), "")
            name = get("manufacturer", "name", "company")
            if not name: continue
            out.append(contract_row(source, i, name=name, address=get("address", "street"), city=get("city"), state=get("state"),
                                    zip_code=get("zip"), source_url=PAGE, source_document=path.name,
                                    source_identifier=get("approval", "number", "id"), status=get("status"),
                                    notes="approval-centric record; one row per approval, not per plant"))
    else:
        entries, cur = [], []
        for ln in "\n".join(pdf_pages_text(path)).splitlines():
            s = ln.strip()
            if not s: continue
            cur.append(s)
            if CSZ.match(s):
                entries.append(cur); cur = []
        for i, e in enumerate(entries, 1):
            m = CSZ.match(e[-1])
            addr = next((l for l in e[1:-1] if re.match(r"^\d+\s", l)), e[1] if len(e) > 2 else "")
            out.append(contract_row(source, i, name=e[0], address=addr, city=m.group(1), state=m.group(2), zip_code=m.group(3),
                                    source_url=PAGE, source_document=path.name, notes="approval-centric record"))
    require(bool(out), path, "no manufacturer rows parsed from the DOS document")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
