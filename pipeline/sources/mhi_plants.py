"""mhi_plants — Manufactured Housing Institute HUD-code plant list (PDF).

MHI publishes a dated "Manufactured Home Plant List" PDF, e.g.
https://www.manufacturedhousing.org/wp-content/uploads/2024/10/Plant-List-10_3_24.pdf. The
file name carries the date, so the registry `url` is the durable pointer: when MHI posts a new
list, updating the url is the versioned registry edit. Registry trap: plant codes are durable
per-facility identifiers — captured in source_identifier.

Layout is parsed from text lines: an entry is a plant-code token (e.g. ALA-011, PFS-655 or a
bare 3–5 digit code) with a name, then address lines ending in "City, ST 12345". Tables, when
pdfplumber finds them, are used first.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import http_get, pdf_tables, pdf_pages_text, contract_row, split_city_state_zip, require

URL = "https://www.manufacturedhousing.org/wp-content/uploads/2024/10/Plant-List-10_3_24.pdf"
CODE = re.compile(r"\b([A-Z]{2,4}-?\d{2,5}[A-Z]?)\b")
CSZ = re.compile(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    url = source.get("url") or URL
    return [http_get(url, archive_dir, url.rsplit("/", 1)[-1])]


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[0]
    out = []
    tables = pdf_tables(path)
    hdr_tables = [t for t in tables if t and any(re.search(r"plant|manufacturer|name", c, re.I) for c in t[0])]
    if hdr_tables:
        pos = 0
        for t in hdr_tables:
            hdr = [c.lower() for c in t[0]]
            col = lambda pat: next((i for i, h in enumerate(hdr) if re.search(pat, h)), None)
            ni, ai, ci, si, zi, ki = col("manufacturer|company|name"), col("address|street"), col("city"), col("state"), col("zip"), col("code|plant\s*#|number|id")
            for cells in t[1:]:
                pos += 1
                name = cells[ni] if ni is not None and ni < len(cells) else ""
                if not name.strip():
                    continue
                g = lambda i: cells[i] if i is not None and i < len(cells) else ""
                city, state, zip_ = g(ci), g(si), g(zi)
                if not state and g(ai):
                    lines = g(ai).split("\n")
                    if len(lines) > 1 and CSZ.match(lines[-1].strip()):
                        city, state, zip_ = split_city_state_zip(lines[-1].strip())
                out.append(contract_row(source, pos, name=name, address=g(ai).split("\n")[0], city=city, state=state, zip_code=zip_,
                                        source_url=source.get("url") or URL, source_document=path.name, source_identifier=g(ki)))
    if not out:
        entries, cur = [], []
        for ln in "\n".join(pdf_pages_text(path)).splitlines():
            s = ln.strip()
            if not s or re.match(r"^(page \d|manufactured home plant list|plant count|updated|as of|state\b)", s, re.I):
                continue
            cur.append(s)
            if CSZ.match(s):
                entries.append(cur); cur = []
        for i, e in enumerate(entries, 1):
            m = CSZ.match(e[-1])
            head = e[0]
            code = next((c for c in CODE.findall(" ".join(e[:2])) if not re.fullmatch(r"[A-Z]{2}-?\d{5}", c)), "")
            name = CODE.sub("", head).strip(" -–|") or head
            addr = next((l for l in e[1:-1] if re.match(r"^\d+\s", l) or re.match(r"^p\.?o\.? box", l, re.I)), e[1] if len(e) > 2 else "")
            out.append(contract_row(source, i, name=name, address=addr, city=m.group(1), state=m.group(2), zip_code=m.group(3),
                                    source_url=source.get("url") or URL, source_document=path.name, source_identifier=code))
    require(bool(out), path, "no plant entries parsed — inspect the PDF layout")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
