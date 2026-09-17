"""mo_psc — Missouri PSC's registered manufacturers: modular (ACTIVE MOD) and HUD-code (ACTIVE HUD).

Found enriching ProMod Manufacturing. Missouri, like Indiana, North Carolina, New York and Georgia,
registers every manufacturer that ships into the state, so both lists are national — the HUD list
alone runs TX 20, AL 15, TN 10, IN 9 — and both carry a street on every row, which only GA DCA
does among the registries we hold. Two files, two segments: ACTIVE MOD is modular buildings, ACTIVE
HUD is HUD-code manufactured homes (NAICS 321991, a core code). Both are in scope and both are read.

Each PDF is one table, one plant per visual line, under the header

    Registration #  Business Name  Address  City  State  Zip  Phone

and it is read by COLUMN: the header words fix seven x-ranges and every word on every line falls
into one. That is what makes "2039 BEXAR AVENUE EAST | HAMILTON" and "6901 BOWMAN ROBERTS ST | FORT
WORTH" unambiguous; read as text, no rule says where the street stops and the city starts.

The URL rotates — the filename carries a date ("ACTIVE MOD 6-2-26") and the previous one 404s — so
the file is discovered from the Manufacturer_Information page rather than pinned. Some rows give a
PO Box, which the contract correctly refuses to turn into a street key: those plants land as T0
leads under their city, which is the honest tier for a mailbox.

`needs_classify: false`: a registration to build modular or HUD-code housing is the attestation.
The registration number rides in source_identifier, which is what makes a plant's row_hash stable
across the file's monthly re-issue.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import US_STATES, LayoutChanged, contract_row, drop_repeated_lines, http_get, pdf_lines, pick, require

INDEX = "https://psc.mo.gov/ManufacturedHousing/Manufacturer_Information"
HEADER = ("Registration", "Business", "Address", "City", "State", "Zip", "Phone")
# The header groups as printed: two of them are two words, and a boundary belongs between the LAST
# word of the left group and the FIRST word of the right — midpoints between first words put the
# Registration/Name boundary inside the name, and "ADVENTURE HOMES LLC" read as "HOMES LLC".
GROUPS = (("Registration", "#"), ("Business", "Name"), ("Address",), ("City",), ("State",), ("Zip",), ("Phone",))
SEGMENT = {"MOD": "modular", "HUD": "HUD-code manufactured homes"}


def _columns(lines: list[list[dict]]) -> list[float]:
    """Column boundaries: the MIDPOINTS between adjacent header words, not the header words' x0.

    These headers are centred over their data, so "ADVENTURE HOMES LLC" begins left of the word
    "Business" and, measured against x0, belonged to the Registration column. The first version did
    exactly that: names read "1 LLC 25786 MINER RD", cities were empty, and the HUD list yielded
    nothing at all. A boundary halfway between two headers is where a column actually changes."""
    for line in lines:
        words = [w["text"] for w in line]
        if all(h in words for h in HEADER):
            def x1_of(word):  return next(w["x1"] for w in line if w["text"] == word)
            def x0_of(word):  return next(w["x0"] for w in line if w["text"] == word)
            bounds = []
            for left, right in zip(GROUPS, GROUPS[1:]):
                last = left[-1] if left[-1] in words else left[0]      # "#" may print as part of "Registration#"
                bounds.append((x1_of(last) + x0_of(right[0])) / 2)
            return bounds
    raise LayoutChanged("no 'Registration # Business Name Address City State Zip Phone' header — the PSC table changed")


def _col(x: float, bounds: list[float]) -> int:
    """Index of the column a word at x belongs to: the number of boundaries to its left."""
    return sum(1 for b in bounds if x >= b)


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    index = http_get(INDEX, archive_dir, "manufacturer-information.html")
    html = index.read_text(encoding="utf-8", errors="replace")
    links = re.findall(r'href="([^"]*ACTIVE%20(?:MOD|HUD)[^"]*\.pdf)"', html, re.I)
    if not links:
        raise LayoutChanged("no ACTIVE MOD / ACTIVE HUD PDF linked from Manufacturer_Information")
    paths = [index]
    for href in sorted(set(links)):
        url = href if href.startswith("http") else "https://psc.mo.gov" + href
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rsplit("/", 1)[-1].replace("%20", " "))
        paths.append(http_get(url, archive_dir, name))
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    pdfs = [p for p in pick(paths, ".pdf") if re.search(r"ACTIVE[_ ]?(MOD|HUD)", p.name, re.I)]
    require(bool(pdfs), paths[0] if paths else Path(INDEX), "no ACTIVE MOD / ACTIVE HUD PDF in the archived folder")
    out, po_box, foreign, bad = [], 0, 0, 0
    for pdf in sorted(pdfs):
        seg = SEGMENT["HUD" if re.search(r"HUD", pdf.name, re.I) else "MOD"]
        raw = pdf_lines(pdf)
        bounds = _columns(raw)
        for line in drop_repeated_lines(raw):
            cols = {i: [] for i in range(len(HEADER))}
            for w in line:
                cols[_col(w["x0"], bounds)].append(w["text"])
            reg, name, addr, city, st, zipc, phone = (" ".join(cols[i]) for i in range(7))
            if not name or not re.fullmatch(r"[A-Z]{2}", st.strip()) or not re.match(r"\d{5}", zipc.strip()):
                bad += 1               # header, footer, page number, or a wrapped fragment
                continue
            st = st.strip()
            if st not in US_STATES:
                foreign += 1
                continue
            if re.match(r"^\s*P\.?\s*O\.?\s*BOX", addr, re.I):
                po_box += 1
            out.append(contract_row(
                source, len(out) + 1, name=name, address=addr, city=city, state=st,
                zip_code=zipc.strip()[:5], source_url=INDEX, source_document=pdf.name,
                source_identifier=reg.strip() or name,
                notes=f"Missouri PSC registered manufacturer, {seg}; registration {reg.strip() or '?'}; phone {phone.strip()}"[:200]))
    require(bool(out), pdfs[0], "PDFs found but no manufacturer row parsed")
    out[0]["notes"] = (out[0]["notes"] + f" | {len(out)} registrations across {len(pdfs)} list(s); {po_box} give a PO Box "
                       f"rather than a street; {foreign} non-US skipped; {bad} non-data lines dropped")[:200]
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
