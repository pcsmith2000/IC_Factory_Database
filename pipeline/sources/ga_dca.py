"""ga_dca — Georgia DCA's directory of approved industrialized-building manufacturers.

Found by accident, enriching ProMod Manufacturing: the search results carried this PDF. It is the
first state registry in the registry that carries a STREET for every manufacturer — Name, Phone,
Address, Occupancy — where IN, NC, NY and CA carry a city at best. And like those, it is a
national roster: Georgia approves everyone who ships into the state, so page one alone runs to
Virginia, Louisiana, South Carolina, Michigan, Wisconsin and California.

The PDF is a browser print of a web table, and its text order is not its visual order — a name
wraps onto the line after its own phone, a ZIP wraps under the occupancy, a "(City, ST)" tag
splits across two lines as "(Leesburg," / "FL)". Reading it as text is a losing game. Reading it
by COLUMN is not: the header words Name / Phone / Address / Occupancy fix four x-ranges, every
word on every page falls into one of them by its x-position, and a record is then just the words
of each column between two record boundaries.

A record boundary is defined by completeness rather than by layout. A record is complete once it
holds a "(City, ST)" tag AND a "City, ST ZIP" in the address column; the next line that brings
either name-column text or address-column text starts a new one. Occupancy, phone and email lines
never start a record, which is what keeps "5410 Fallowater Ln Roanoke, VA 24018" — printed
BEFORE the name "VFP (Duffield,VA)" it belongs to — from being glued onto the record above it.

Occupancy is the directory's statement of what a manufacturer is APPROVED TO SELL INTO GEORGIA,
which is not the same as what it makes. The first version of this parser dropped every
manufacturer approved only for Storage, Hazardous or Utility as shed-and-enclosure makers — and
dropped Trachte with them, a pre-engineered metal building manufacturer the classifier prompt names
by name, because Georgia has approved it for storage occupancies. So occupancy is carried in
`notes` as evidence and the source is `needs_classify: true`: the classifier draws the boundary
per row and records its reason, which is attributable in a way a field-filter is not. U.S.
Chemical Storage will still be NOT-IC; it will say why.

The row carries what the directory carries beyond the contract: occupancy, and the website and
email where printed, in `notes`. The contract has no `website` column yet; when it does, this is
one of the sources that fills it.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import US_STATES, LayoutChanged, contract_row, drop_repeated_lines, pdf_lines, pick, require

URL = "https://dca.georgia.gov/document/fact-sheets/approved-industrialized-building-manufacturers/download"
HEADER = ("Name", "Phone", "Address", "Occupancy")
TAG = re.compile(r"\(\s*([A-Za-z .'\-]+?)\s*,\s*([A-Za-z]{2})\s*\)")     # "(Buford, Ga)" — the state is not always upper-cased
# Street is GREEDY and the city is one to three words: a non-greedy street handed "Iris Drive SE
# Conyers" to the city. Three words is the fallback; the record's own "(City, ST)" tag decides when
# it can — see _split_address.
CITY_ST_ZIP = re.compile(r"^(?P<street>.+)\s+(?P<city>[A-Za-z.'\-]+(?:\s+[A-Za-z.'\-]+){0,2})\s*,\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:\s*-\s*\d{4})?\s*$")
WWW_FRAG = re.compile(r"\bwww\.\S*|https?://\S*")                    # a wrapped URL leaves "http://" or "www.panel-" behind
TWO_ADDRESSES = re.compile(r"\b[A-Z]{2}\s+\d{5}\b.*\b[A-Z]{2}\s+\d{5}\b")
PHONE = re.compile(r"\b\d{3}-\d{3}-\d{4}\b")
EMAIL = re.compile(r"\S+@\S+")
WEB = re.compile(r"\b(?:www\.)?[a-z0-9-]+\.(?:com|net|org|us)\b(?:/\S*)?", re.I)
BUILDING = re.compile(r"dwelling|assembly|business|educational|institutional|mercantile|factory|industrial|daycare|residential", re.I)
NON_BUILDING = re.compile(r"storage|hazardous|utility", re.I)


def _columns(lines: list[list[dict]]) -> list[float]:
    """x0 of each header word on the first header line. Column k is [x_k, x_k+1).

    pdf_lines returns a FLAT list of visual lines across every page, each a list of words — not a
    list of pages. The header is read before furniture is dropped, because dropping it is exactly
    what drop_repeated_lines does to a header that repeats on every page."""
    for line in lines:
        words = [w["text"] for w in line]
        if all(h in words for h in HEADER):
            xs = {w["text"]: w["x0"] for w in line if w["text"] in HEADER}
            return [xs[h] for h in HEADER]
    raise LayoutChanged("no Name / Phone / Address / Occupancy header on page 1 — the directory's layout changed")


def _col(x: float, bounds: list[float]) -> int:
    k = 0
    for i, b in enumerate(bounds):
        if x >= b - 2:
            k = i
    return k


def _addr_text(words: list[str]) -> str:
    """The address column with the email, website and phone that share it stripped out.

    Completeness was judged on the raw column, and "1300 Davenport Drive Minden, LA 71055
    engineering@fibrebond.com" does not end in a ZIP — so Fibrebond never read as complete and
    Frey-Moss Structures was glued onto it, name, address and occupancy. Six records merged that way."""
    s = " ".join(words)
    for pat in (EMAIL, PHONE, WWW_FRAG, WEB):
        s = pat.sub(" ", s)
    return " ".join(s.split())


def _split_address(addr: str, tag_city: str) -> re.Match | None:
    """Prefer the record's own "(City, ST)" tag as the city when the address ends with it."""
    if tag_city:
        m = re.match(r"^(?P<street>.+?)\s+(?P<city>" + re.escape(tag_city) + r")\s*,\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:\s*-\s*\d{4})?\s*$", addr, re.I)
        if m:
            return m
    return CITY_ST_ZIP.match(addr)


def _records(paths_lines: list[list[dict]], bounds: list[float]) -> list[dict]:
    recs, cur = [], None

    def complete(r):
        return r is not None and TAG.search(" ".join(r["name"])) and CITY_ST_ZIP.match(_addr_text(r["addr"]))

    for line in paths_lines:
        cols = {0: [], 1: [], 2: [], 3: []}
        for w in line:
            cols[_col(w["x0"], bounds)].append(w["text"])
        text = " ".join(w["text"] for w in line)
        if not text.strip() or text.startswith(HEADER[0]) and HEADER[1] in text or "about:blank" in text or text.startswith("Industrialized Buildings"):
            continue
        starts = bool(cols[0]) or bool(cols[2])
        if cur is None or (complete(cur) and starts):
            if cur is not None:
                recs.append(cur)
            cur = {"name": [], "phone": [], "addr": [], "occ": []}
        cur["name"] += cols[0]; cur["phone"] += cols[1]; cur["addr"] += cols[2]; cur["occ"] += cols[3]
    if cur is not None:
        recs.append(cur)
    return recs


def _clean(rec: dict) -> dict | None:
    name_text = " ".join(rec["name"])
    tag = TAG.search(name_text)
    if not tag:
        return None
    hq_city, hq_state = tag.group(1).strip(), tag.group(2).upper()
    name = TAG.sub(" ", name_text)
    phones = PHONE.findall(name_text + " " + " ".join(rec["phone"]) + " " + " ".join(rec["addr"]))
    emails = EMAIL.findall(name_text + " " + " ".join(rec["phone"]) + " " + " ".join(rec["addr"]) + " " + " ".join(rec["occ"]))
    for pat in (PHONE, EMAIL):
        name = pat.sub(" ", name)
    web = [w for w in WEB.findall(name + " " + " ".join(rec["addr"]) + " " + " ".join(rec["occ"])) if "@" not in w]
    name = WEB.sub(" ", name); name = WWW_FRAG.sub(" ", name)
    name = " ".join(name.split()).strip(" ,")
    addr_text = _addr_text(rec["addr"])
    m = _split_address(addr_text, hq_city)
    occ = " ".join(" ".join(rec["occ"]).split())
    occ = EMAIL.sub(" ", occ); occ = WEB.sub(" ", occ); occ = " ".join(occ.split()).strip(" ,")
    return {"name": name, "hq_city": hq_city, "hq_state": hq_state, "addr_raw": addr_text,
            "street": m.group("street").strip() if m else "", "city": m.group("city").strip() if m else "",
            "state": m.group("state") if m else "", "zip": m.group("zip") if m else "",
            "phone": phones[0] if phones else "", "email": emails[0] if emails else "",
            "web": web[0] if web else "", "occupancy": occ}


def parse(paths: list[Path], source: dict) -> list[dict]:
    pdfs = [p for p in pick(paths, ".pdf") if "approved" in p.name.lower()] or pick(paths, ".pdf")
    require(bool(pdfs), paths[0] if paths else Path(URL), "no PDF in the archived folder — upload the approved-manufacturers PDF")
    raw = pdf_lines(pdfs[0])
    bounds = _columns(raw)
    lines = drop_repeated_lines(raw)          # the browser print's date/URL header and the column header, every page
    recs = [_clean(r) for r in _records(lines, bounds)]
    recs = [r for r in recs if r]
    out, storage_only, no_addr, foreign, merged = [], 0, 0, 0, 0
    for r in recs:
        # The browser print sometimes emits a name's tail on the line AFTER its record is complete,
        # and the record that follows then has no name line of its own to start on — so two records
        # arrive as one, with two addresses in the address column. Publishing that as one plant with
        # the first address and a doubled name would be wrong twice; it is held back and counted.
        if TWO_ADDRESSES.search(r["addr_raw"]):
            merged += 1
            continue
        if not r["street"] or not r["state"]:
            no_addr += 1
            continue
        if r["state"] not in US_STATES:
            foreign += 1
            continue
        if r["occupancy"] and NON_BUILDING.search(r["occupancy"]) and not BUILDING.search(r["occupancy"]):
            storage_only += 1              # kept; the classifier decides, with the occupancy in front of it
        notes = f"GA DCA approved manufacturer; occupancy: {r['occupancy'] or 'not stated'}"
        if r["web"]: notes += f"; website: {r['web']}"
        if r["hq_city"] and (r["hq_city"].lower() != r["city"].lower()): notes += f"; directory tags HQ as {r['hq_city']}, {r['hq_state']}"
        out.append(contract_row(source, len(out) + 1, name=r["name"], address=r["street"], city=r["city"],
                                state=r["state"], zip_code=r["zip"], source_url=URL, source_document=pdfs[0].name,
                                source_identifier=r["phone"] or r["email"] or r["name"], notes=notes[:200]))
    require(bool(out), pdfs[0], f"{len(recs)} records read and none had a street in a US state")
    out[0]["notes"] = (out[0]["notes"] + f" | {len(out)} manufacturers of {len(recs)} records; "
                       f"{storage_only} approved for storage/hazardous/utility occupancies only (kept for the classifier); "
                       f"{no_addr} without a parseable address; {foreign} non-US; {merged} held back as two records printed as one")[:200]
    return out
