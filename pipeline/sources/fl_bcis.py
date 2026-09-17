"""fl_bcis — Florida BCIS Manufactured (Modular) Buildings: organisation search (manufacturers).

Registry traps: POST-only search that defeats a plain fetch; ~850 rows are names only (no
address) — they can never be promoted past T0 on their own, and the fetcher does not invent one.

The search is an ASP.NET WebForms page under https://floridabuilding.org/mb/. The fetcher does
the WebForms dance deterministically: GET the search page, keep every hidden input
(__VIEWSTATE, __EVENTVALIDATION, ...), set the organisation-type field to Manufacturer when one
is present, POST the search button, parse the results table. Any step that does not find what
it expects raises LayoutChanged with the archived page — the form field names are discovered,
not assumed, so the first live run is the check.
"""
from __future__ import annotations
import re, urllib.parse
from pathlib import Path
from html import unescape
from ._common import http_get, html_tables, contract_row, require, iso_date, LayoutChanged

MENU = "https://floridabuilding.org/mb/mb_default.aspx"


def _form(html: str) -> tuple[str, dict]:
    m = re.search(r'<form[^>]+action="([^"]*)"[^>]*>(.*?)</form>', html, re.I | re.S)
    if not m:
        return "", {}
    fields = {}
    for tag in re.findall(r"<input[^>]+>", m.group(2), re.I):
        n = re.search(r'name="([^"]+)"', tag); v = re.search(r'value="([^"]*)"', tag)
        if n and (re.search(r'type="hidden"', tag, re.I) or v):
            fields[n.group(1)] = v.group(1) if v else ""
    for sel in re.findall(r"<select[^>]+name=\"([^\"]+)\"[^>]*>(.*?)</select>", m.group(2), re.I | re.S):
        name, body = sel
        opts = re.findall(r'<option[^>]+value="([^"]*)"[^>]*>(.*?)</option>', body, re.I | re.S)
        manu = next((v for v, t in opts if re.search(r"manufactur", t, re.I)), None)
        fields[name] = manu if manu is not None else (opts[0][0] if opts else "")
    return m.group(1), fields


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    menu = http_get(source.get("url") or MENU, archive_dir, "menu.html")
    html = menu.read_text(encoding="utf-8", errors="replace")
    links = [h for h, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S)
             if re.search(r"organi[sz]ation|manufacturer", re.sub("<[^>]+>", " ", t), re.I) and re.search(r"search", h + t, re.I)]
    require(bool(links), menu, "no organisation/manufacturer search link on the MB menu")
    search_url = urllib.parse.urljoin(MENU, links[0])
    page = http_get(search_url, archive_dir, "search_form.html")
    action, fields = _form(page.read_text(encoding="utf-8", errors="replace"))
    require("__VIEWSTATE" in fields, page, "search page is not the WebForms form expected (no __VIEWSTATE)")
    btn = next((k for k in fields if re.search(r"search|find|submit", k, re.I) and not k.startswith("__")), None)
    require(btn is not None, page, "no search button input found in the form")
    body = urllib.parse.urlencode(fields).encode()
    results = http_get(urllib.parse.urljoin(search_url, action or search_url), archive_dir, "results.html", data=body,
                       headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": search_url})
    return [results]


# Each organisation in the results grid is anchored by a uniquely-id'd name link:
#
#   <a id="grdReport__ctl2_hlnkOrgName" href="...">A &amp; A Sheet Metal Products</a>
#     <b>Org Type </b>Modular Unit Manufacturer
#     <b>FBC Organization Number </b>MFT3685
#     <b>Website </b><a href="http://www.securallproducts.com/">www.securallproducts.com/</a>
#   <span id="grdReport__ctl2_lblValidFromDate">07/27/2004</span> ... lblValidToDate, lblOrgStatus
#
# Taking the largest <table> instead yielded 37 rows of run-together page text, because the grid
# nests tables inside its cells and a saved listing concatenates all 48 pages, each with its own
# grdReport table and its own _ctl2.._ctl21 numbering. Splitting on the name anchors ignores both
# the nesting and the pagination and recovers every record.
ANCHOR = re.compile(r'<a[^>]+id="[^"]*hlnkOrgName"[^>]*>(.*?)</a>', re.I | re.S)
FIELD = lambda label: re.compile(r"<b>\s*" + label + r"\s*</b>\s*([^<]*)", re.I)
ORG_TYPE, ORG_NUM = FIELD("Org Type"), FIELD("FBC Organization Number")
WEBSITE = re.compile(r"<b>\s*Website\s*</b>\s*<a[^>]+href=\"([^\"]+)\"", re.I)
SPAN = lambda name: re.compile(r'id="[^"]*' + name + r'"[^>]*>(.*?)</span>', re.I | re.S)
VALID_TO, STATUS = SPAN("lblValidToDate"), SPAN("lblOrgStatus")
# "Modular Unit Manufacturer" is a plant; "Manufacturer Additional Facilities" is a further plant of
# one. Every other organisation type on this search is a certifier, inspector or plan reviewer.
PLANT_TYPES = re.compile(r"manufactur", re.I)
TAGS = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(TAGS.sub(" ", s or ""))).strip()


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[-1]
    html = path.read_text(encoding="utf-8", errors="replace")
    spans = [m for m in ANCHOR.finditer(html)]
    require(bool(spans), path, "no organisation name anchors (hlnkOrgName) in the results page")
    out = []
    for i, m in enumerate(spans, 1):
        block = html[m.end():spans[i].start() if i < len(spans) else len(html)]
        one = lambda rx: (rx.search(block).group(1).strip() if rx.search(block) else "")
        org_type = _text(one(ORG_TYPE))
        if not PLANT_TYPES.search(org_type):
            continue
        expiry = iso_date(_text(one(VALID_TO)))
        out.append(contract_row(
            source, i, name=_text(m.group(1)), source_url=MENU, source_document=path.name,
            source_identifier=_text(one(ORG_NUM)), status=_text(one(STATUS)),
            expiry_date=expiry, status_basis="dated_expiry" if expiry else "explicit_status_field",
            # The listing publishes no plant address, but it does publish the manufacturer's own
            # website, which is the only address-discovery channel any source here gives us.
            notes="; ".join(x for x in (f"org_type={org_type}" if org_type else "",
                                        f"website={one(WEBSITE)}" if one(WEBSITE) else "",
                                        "names-only source: no plant address published") if x)))
    require(bool(out), path, "organisation anchors found but none had a manufacturer org type")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
