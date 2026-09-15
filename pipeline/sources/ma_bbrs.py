"""ma_bbrs — Massachusetts BBRS manufactured buildings program: certified manufacturers (PDF).

Program page: https://www.mass.gov/info-details/manufactured-building-program. The certified /
approved manufacturers document is discovered from that page (mass.gov serves documents at
/doc/<slug>/download, the slug is not stable). Registry trap: undated PDF that still lists
manufacturers defunct 10+ years — status_basis stays on_current_list, liveness is not inferred here.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import http_get, pdf_pages_text, contract_row, split_city_state_zip, require

PAGE = "https://www.mass.gov/info-details/manufactured-building-program"
CSZ = re.compile(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    page = http_get(source.get("url") or PAGE, archive_dir, "program.html")
    html = page.read_text(encoding="utf-8", errors="replace")
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S)
    cands = [h for h, t in links if re.search(r"(certified|approved|list of).{0,40}manufacturer|manufacturer.{0,40}list", re.sub("<[^>]+>", " ", t), re.I)]
    require(bool(cands), page, "no certified/approved manufacturers document linked from the BBRS program page")
    url = cands[0] if cands[0].startswith("http") else "https://www.mass.gov" + cands[0]
    return [http_get(url, archive_dir, "certified_manufacturers.pdf")]


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[0]
    entries, cur = [], []
    for ln in "\n".join(pdf_pages_text(path)).splitlines():
        s = ln.strip()
        if not s or re.match(r"^(page \d|commonwealth of|board of building|manufactured building|certified manufacturers|updated|as of)", s, re.I):
            continue
        cur.append(s)
        if CSZ.match(s):
            entries.append(cur); cur = []
    require(bool(entries), path, "no 'City, ST zip' lines — PDF layout changed")
    out = []
    for i, e in enumerate(entries, 1):
        m = CSZ.match(e[-1]); city, state, zip_ = m.group(1), m.group(2), m.group(3)
        addr = next((l for l in e[1:-1] if re.match(r"^\d+\s", l) or re.match(r"^p\.?o\.? box", l, re.I)), e[1] if len(e) > 2 else "")
        cert = next((x for x in re.findall(r"\b(MA-?\d{2,5}|#\s*\d{2,5})\b", " ".join(e))), "")
        out.append(contract_row(source, i, name=e[0], address=addr, city=city, state=state, zip_code=zip_,
                                source_url=PAGE, source_document=path.name, source_identifier=cert.lstrip("# ")))
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
