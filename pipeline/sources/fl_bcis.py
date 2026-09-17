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
from ._common import http_get, html_tables, contract_row, require, LayoutChanged

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


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[-1]
    html = path.read_text(encoding="utf-8", errors="replace")
    tables = [t for t in html_tables(html) if len(t) > 5]
    require(bool(tables), path, "no results table with more than 5 rows — the POST did not return the manufacturer list")
    table = max(tables, key=len)
    hdr = [c.lower() for c in table[0]]
    name_i = next((i for i, h in enumerate(hdr) if re.search(r"name|organi[sz]ation|manufacturer", h)), 0)
    get = lambda cells, pat: next((cells[i] for i, h in enumerate(hdr) if re.search(pat, h) and i < len(cells)), "")
    out = []
    for i, cells in enumerate(table[1:], 1):
        if not cells or not cells[name_i].strip():
            continue
        out.append(contract_row(source, i, name=cells[name_i], address=get(cells, "address|street"), city=get(cells, "city"),
                                state=get(cells, "state"), zip_code=get(cells, "zip"), source_url=MENU, source_document=path.name,
                                source_identifier=get(cells, "number|id|cert"), status=get(cells, "status|type"),
                                notes="" if get(cells, "address|street") else "names-only source: no plant address published"))
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
