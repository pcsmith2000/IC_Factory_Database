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

# Repeated on every page of the PDF; carried into an entry it becomes a facility called
# "Approved Manufacturers".
_NOISE = re.compile(r"^(approved manufacturers|ca number\s+manufacturer|page \d+)", re.I)
# LARA writes phones as (715) 384-2161, 407-790-2750 and a few malformed variants.
_PHONE = re.compile(r"\s*(?:\(\d{2,3}\)\s*\d{3}-\d{4}|\d{3}-\d{3}-\d{4})\s*$")
_POBOX = re.compile(r"\s+P\.?O\.?\s*Box\s+\d+\s*$", re.I)
_CA = re.compile(r"^(\d{2,5})\s+(.+)$")
# Greedy, and the tail must end in a street type: a company name can hold a number too
# ("Bristlecone Ventures 2 LLC 7717 Gilbert Road" splits at 7717, not at 2).
_STREET_WORD = (r"(?:St(?:reet)?|Rd|Road|Ave(?:nue)?|Dr(?:ive)?|Blvd|Boulevard|Ln|Lane|Way|Ct|Court|"
                r"Hwy|Highway|Cir(?:cle)?|Pkwy|Parkway|Trail|Trl|Loop|Row|Pl(?:ace)?|Terrace|Pike|Park)")
_INLINE_STREET = re.compile(rf"^(.*\S)\s+(\d+\s+.*\b{_STREET_WORD}\b\.?)$", re.I)
_INLINE_STREET_ANY = re.compile(r"^(.*?\S)\s+(\d+\s+\S.*)$")


def _split_name_line(line: str, *, want_street: bool) -> tuple[str, str, str]:
    """"<CA number> <name> [PO Box n] [phone]" -> (name, ca_number, street_if_inline).

    want_street is set only when the entry carried no separate street line, because a few rows run
    the street onto this one. It is never guessed otherwise: plenty of real names contain a number
    ("4 Square Modular 1 LLC", "Bristlecone Ventures 2 LLC"), and splitting those on the first digit
    turns the company into an address.
    """
    rest = _PHONE.sub("", line.strip())
    m = _CA.match(rest)
    ca, rest = (m.group(1), m.group(2)) if m else ("", rest)
    rest = _POBOX.sub("", rest).strip()
    street = ""
    if want_street:
        m = _INLINE_STREET.match(rest) or _INLINE_STREET_ANY.match(rest)
        if m:
            rest, street = m.group(1).strip(), m.group(2).strip()
    return rest, ca, street


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
        # LARA lays each plant out over three lines, and NOT name-first:
        #     425 W McMillan Street                              <- street
        #     122 Wisconsin Homes Inc PO Box 250 (715) 384-2161  <- CA number, name, PO box, phone
        #     Marshfield, WI 54449                               <- city, state, zip
        # Taking line 0 as the name (the generic shape) yields a street as the facility name for
        # every row. The locality line is the reliable anchor, so read backwards from it: the name
        # line is always second-to-last and the street immediately precedes it.
        lines = [x.strip() for x in "\n".join(pdf_pages_text(path)).splitlines() if x.strip()]
        lines = [x for x in lines if not _NOISE.match(x)]
        entries, cur = [], []
        for s_ in lines:
            cur.append(s_)
            if CSZ.match(s_):
                entries.append(cur); cur = []
        for i, e in enumerate(entries, 1):
            m = CSZ.match(e[-1]); city, state, zip_ = m.group(1), m.group(2), m.group(3)
            if len(e) < 2:
                continue
            # Two rows invert the layout: the name and street share one line and the CA number sits
            # on the next with only the phone beside it, so the usual second-to-last line carries
            # no name at all. (Both also put a dba on the locality line, which leaves the city on
            # those two rows prefixed with the trading name — not separable by any rule that does
            # not also mangle ordinary rows, so it is left verbatim.)
            if len(e) >= 3 and _PHONE.sub("", e[-2]).strip().isdigit():
                name, _, street = _split_name_line(e[-3], want_street=True)
                ca = _PHONE.sub("", e[-2]).strip()
            else:
                name, ca, street = _split_name_line(e[-2], want_street=len(e) < 3)
                if len(e) >= 3:
                    street = e[-3]
            out.append(contract_row(source, i, name=name, address=street, city=city, state=state, zip_code=zip_,
                                    source_url=PAGE, source_document=path.name, source_identifier=ca))
    require(bool(out), path, "no manufacturer rows parsed")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
