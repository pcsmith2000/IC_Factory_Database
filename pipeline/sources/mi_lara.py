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

# The PDF lays each approved manufacturer out as three lines, and pdf text extraction interleaves
# them so that the street arrives BEFORE the line carrying the CA number and the name:
#
#     425 W McMillan Street                                <- street
#     122 Wisconsin Homes Inc PO Box 250 (715) 384-2161    <- CA number, name, mailing box, phone
#     Marshfield, WI 54449                                 <- city, state, zip
#
# Grouping on the city line and calling the first line the name (as this parser first did) put the
# street in name_verbatim and the "122 Wisconsin Homes Inc PO Box 250" string in address_verbatim,
# which then geocoded to a town centroid for all 166 rows. The middle line is the reliable anchor:
# its leading CA number is unique and ascending, and the locality line always follows it. Anchoring
# on a phone number instead loses the four plants whose phone is absent, unbracketed or malformed;
# anchoring on a leading number alone invents a plant from "730 Ekastown Road", which is CID
# Associates' street. Requiring both — a leading number AND a locality line directly below — takes
# every real entry and no false one. A name too long for the column wraps onto the street or city
# line, so each is split apart again.
FURNITURE = {"approved manufacturers", "ca number manufacturer address telephone number"}
# (715) 384-2161, 717-440-5497 and the malformed (20) 845-3100 all appear in the file
PHONE = re.compile(r"\(?\d{2,3}\)?[\s-]*\d{3,4}-\d{4}\s*$")
ANCHOR = re.compile(r"^(\d{2,4})\s+(.*)$")
POBOX = re.compile(r"\bP\.?\s?O\.?\s?Box\s+\d+\b", re.I)
STREET = re.compile(r"^(?:No\.\s*)?\d+[A-Za-z]?\s+\S|^[NSEW]\s?\d+\s")
NAME_STREET = re.compile(r"^(.*?[A-Za-z])\s+((?:No\.\s*)?\d+\s+[A-Z].*)$")
NAME_CITY = re.compile(r"^(.*?[a-z])\s+([A-Z][^,]*,.*)$")
SUFFIX = re.compile(r"^((?:Co|Ltd|Inc|LLC|LLP|Corporation|Corp|Company|Partnership|Group)\b\.?\s*)+", re.I)
FOREIGN = re.compile(r"\b(Canada|China|Mexico|Novia Scotia|Nova Scotia|Alberta|Ontario|Manitoba)\b", re.I)


def _is_locality(line: str) -> bool:
    """A 'City, ST 12345' line, or a foreign one ending in a postal code."""
    return bool(CSZ.match(line) or re.search(r",\s*[A-Za-z .]+\s+[A-Z0-9][A-Z0-9 -]{3,}$", line))


def _blocks(path: Path) -> list[dict]:
    """One dict per approved manufacturer, anchored on the phone/CA-number line."""
    lines = [l.strip() for l in "\n".join(pdf_pages_text(path)).splitlines() if l.strip()]
    lines = [l for l in lines if l.lower() not in FURNITURE]
    anchors = [i for i, l in enumerate(lines)
               if ANCHOR.match(l) and i + 1 < len(lines) and _is_locality(lines[i + 1])]
    out, consumed_to = [], -1
    for i in anchors:
        ca, rest = ANCHOR.match(lines[i]).groups()
        phone = PHONE.search(rest).group(0) if PHONE.search(rest) else ""
        rest = PHONE.sub("", rest).strip()
        box = POBOX.search(rest)
        rest = POBOX.sub("", rest).strip(" ,")
        street, frags = "", []
        if i - 1 > consumed_to:                      # the line above is the street
            prev = lines[i - 1]
            if STREET.match(prev):
                street = prev
            elif m := NAME_STREET.match(prev):       # ... unless a wrapped name shares it
                frags.append(m.group(1)); street = m.group(2)
            else:
                frags.append(prev)
            frags.extend(lines[consumed_to + 1:i - 1])
        city_line = lines[i + 1] if i + 1 < len(lines) else ""
        if not CSZ.match(city_line):                 # the line below is city/state/zip
            if m := NAME_CITY.match(city_line):      # ... unless a wrapped name shares it too
                frags.append(m.group(1)); city_line = m.group(2)
        if m := SUFFIX.match(city_line):             # "Co Ltd Pudong, Shangahi" -> suffix is name
            frags.append(m.group(0).strip()); city_line = city_line[m.end():]
        if not street and (m := NAME_STREET.match(rest)):
            rest, street = m.group(1), m.group(2)
        m = CSZ.match(city_line)
        city, state, zip_ = (m.group(1), m.group(2), m.group(3)) if m else (city_line, "", "")
        consumed_to = i + 1
        out.append({"ca": ca, "name": " ".join([rest, *frags]).strip() if rest else " ".join(frags).strip(),
                    "street": street, "city": city, "state": state, "zip": zip_,
                    "notes": " ".join(x for x in (box.group(0) if box else "", phone) if x)})
    return out


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
        for i, b in enumerate(_blocks(path), 1):
            # The PO box is a mailing address, never the plant, so it stays out of address_verbatim
            # (which is what gets geocoded) and is recorded in notes alongside the phone.
            out.append(contract_row(source, i, name=b["name"], address=b["street"], city=b["city"],
                                    state=b["state"], zip_code=b["zip"], source_identifier=b["ca"],
                                    country="" if FOREIGN.search(b["city"]) else "US",
                                    notes=b["notes"], source_url=PAGE, source_document=path.name))
    require(bool(out), path, "no manufacturer rows parsed")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
