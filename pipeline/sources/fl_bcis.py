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

def _iso(d: str) -> str:
    """MM/DD/YYYY -> ISO, else "" — BCIS leaves the date blank on pending applications."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", (d or "").strip())
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else ""


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
             if re.search(r"organi[sz]ation|manufacturer", re.sub("<[^>]+>", " ", t), re.I)
             and re.search(r"se?a?rch", h + t, re.I)]   # the menu link is mb_org_srch.aspx — "srch", not "search"
    require(bool(links), menu, "no organisation/manufacturer search link on the MB menu")
    search_url = urllib.parse.urljoin(MENU, links[0])
    page = http_get(search_url, archive_dir, "search_form.html")
    form_html = page.read_text(encoding="utf-8", errors="replace")
    action, fields = _form(form_html)
    require("__VIEWSTATE" in fields, page, "search page is not the WebForms form expected (no __VIEWSTATE)")
    btn = next((k for k in fields if re.search(r"search|find|submit", k, re.I) and not k.startswith("__")), None)
    if btn is None:
        # No submit input on this page: the search button is an anchor that posts back through
        # __doPostBack('btnSearch$lnkBtnLingual', ''). Driving the postback IS the submit — the
        # target is read off the page, not assumed, so a renamed control still fails loudly.
        m = re.search(r"__doPostBack\('([^']*(?:search|find|submit)[^']*)'", form_html, re.I)
        require(m is not None, page, "no search button input and no __doPostBack search target in the form")
        fields["__EVENTTARGET"], fields["__EVENTARGUMENT"] = m.group(1), ""
    body = urllib.parse.urlencode(fields).encode()
    results = http_get(urllib.parse.urljoin(search_url, action or search_url), archive_dir, "results.html", data=body,
                       headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": search_url})
    _reject_error_page(results)
    return [results]


def _reject_error_page(path: Path) -> None:
    """BCIS answers a failed postback with HTTP 200 and a 'System Error' page that still contains
    tables — so an unguarded parse turns the error text into rows. Refuse it here instead."""
    body = path.read_text(encoding="utf-8", errors="replace")   # whole page: BCIS renders the error mid-document
    require(not re.search(r"system error|unexpected system error", body, re.I), path,
            "BCIS returned its System Error page, not results — the WebForms postback was rejected "
            "(ASP.NET session state). Export the organisation search by hand and upload it to "
            "ic-sources/fl_bcis/<date>/")


# The results grid is an ASP.NET DataGrid whose controls carry stable ids: one
# grdReport__ctl<N>_hlnkOrgName anchor per organisation, with its status, valid-to date and FBC
# number in siblings keyed by the same _ctl<N>_. Reading those ids is far steadier than picking a
# table by size — the page nests ~800 layout tables and the largest of them is a layout wrapper,
# so the generic "biggest table" shape returned the page furniture as rows.
_REC = re.compile(r'id="grdReport__ctl(\d+)_hlnkOrgName"[^>]*>(.*?)</a>', re.I | re.S)
_FIELD = r'id="grdReport__ctl{n}_{f}"[^>]*>(.*?)</span>'
_ORGNUM = re.compile(r"FBC\s*Organization\s*Number\s*</b>\s*([A-Za-z0-9\-]+)", re.I)
# The grid's Administrator cell is three things in one: the registered contact's name, their
# phone, and a mailto. 935 of the 959 organisations carry a phone there and 800 an email, and
# this source published neither until 2026-09-19 — it was read as "names only" because it
# publishes no plant ADDRESS, which is a different absence.
#
# It is a registered contact, not a plant switchboard, and the note on every row says so: the
# person who filed the registration with Florida. That is a real business contact for the
# organisation and the state publishes it deliberately, but it should not be read as the number
# on the factory door.
_ADMIN_PHONE = re.compile(r"(?:\((\d{3})\)\s*|\b(\d{3})[.\-\s])(\d{3})[.\-\s]?(\d{4})\b")
_ADMIN_EMAIL = re.compile(r'mailto:([^"?\s>]+)', re.I)
_ORGTYPE = re.compile(r"Org\s*Type\s*</b>\s*([^<]+)", re.I)
_PAGES = re.compile(r'id="pagTopPager_lblCurrentPage"[^>]*>(\d+)</span>\s*&nbsp;/\s*<span[^>]*id="pagTopPager_lblTotalPages"[^>]*>(\d+)</span>', re.I | re.S)


def _untag(x: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x or "")).replace("&amp;", "&").strip()


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = paths[-1]
    _reject_error_page(path)
    html = path.read_text(encoding="utf-8", errors="replace")
    require("grdReport" in html, path, "no grdReport results grid on the page — this is not the "
                                       "organisation-list result (see docs/manual-uploads.md)")
    recs = list(_REC.finditer(html))
    require(bool(recs), path, "grdReport is present but holds no organisation rows")
    pg = _PAGES.search(html)
    page_note = ""
    if pg and len(recs) < int(pg.group(2)):
        # The grid renders every row and paginates in the browser, so a saved page normally holds
        # the whole list even though the pager still reads "1 / 48". Fewer rows than there are
        # pages is the case that cannot be a full export — flag only that, on every row, rather
        # than labelling a complete 959-row save "partial" because a widget says page 1.
        page_note = f"PARTIAL EXPORT: {len(recs)} rows saved but the pager reports {pg.group(2)} pages"
    out = []
    for i, m in enumerate(recs, 1):
        n, name = m.group(1), _untag(m.group(2))
        if not name:
            continue
        block = html[m.end():recs[i].start() if i < len(recs) else len(html)]
        fld = lambda f: _untag((re.search(_FIELD.format(n=n, f=f), block, re.I | re.S) or [None, ""])[1]
                               if re.search(_FIELD.format(n=n, f=f), block, re.I | re.S) else "")
        num = _ORGNUM.search(block)
        typ = _ORGTYPE.search(block)
        expiry = fld("lblValidToDate")
        admin = fld("lblAdministrator")
        admin_raw = (re.search(_FIELD.format(n=n, f="lblAdministrator"), block, re.I | re.S) or [None, ""])
        admin_raw = admin_raw[1] if not hasattr(admin_raw, "group") else admin_raw.group(1)
        ph = _ADMIN_PHONE.search(admin or "")
        em = _ADMIN_EMAIL.search(admin_raw or "")
        notes = "names-only source: no plant address published"
        if ph or em:
            notes += "; phone/email are the REGISTERED ADMINISTRATOR's, not a plant switchboard"
        if typ:
            notes += f"; org type: {_untag(typ.group(1))}"
        if page_note:
            notes += f"; {page_note}"
        out.append(contract_row(source, i, name=name, address="", city="", state="", zip_code="",
                                source_url=MENU, source_document=path.name,
                                source_identifier=num.group(1) if num else "",
                                phone="".join(g for g in ph.groups() if g) if ph else "",
                                email=em.group(1) if em else "",
                                status=fld("lblOrgStatus"), expiry_date=_iso(expiry),
                                # The registry calls this source on_current_list, but the grid
                                # carries a real valid-to date and a status of its own, and most
                                # rows are Expired/Denied/Inactive — treating presence on the list
                                # as liveness would mark 400+ dead registrations active.
                                status_basis="dated_expiry" if _iso(expiry) else None, notes=notes))
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
