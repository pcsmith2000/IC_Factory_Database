"""mbma — Metal Building Manufacturers Association building-systems members.

Pre-engineered metal buildings are a core segment — 332311 is one of the four core NAICS codes —
and Steel Structural Components is 10 rows of the ADL control list that no source we hold reaches.
MBMA's building-systems membership is the roster of those plants: to join, a manufacturer must be
AC472-accredited through the International Accreditation Service, so membership is an attestation
about a manufacturing plant rather than a trade-show listing. Hence `needs_classify: false`.

Getting at it took three corrections, each recorded because each looked like a dead end:

  /membership/member-directory and /members  404. Both are the obvious guesses and both are wrong.
  /building-systems-directory                200, but a plain fetch returns 35KB of navigation
                                             shell with no member in it.
  rendered with Chromium                     still 36KB — which reads like a second dead end and
                                             is not. The members ARE there; the address regex that
                                             said otherwise was looking for "Batesville, MS 38606"
                                             and the page writes "Batesville, Mississippi 38606".

So the state is spelled out, which is also how the non-US members give themselves away: the filter
offers Alberta, Ontario and Yukon beside the states, and a member in Manitoba is not a US plant.
Rows are kept only when the spelled-out state is one of the fifty plus DC.

The list is an infinite scroll — `views-infinite-scroll-content-wrapper` — so the first render
holds one page of it. That is the same failure this repo just found in sipa, where one page of five
was published as the whole segment for three weeks. Here the page is scrolled until the member
count stops growing, and a scroll that never settles is an error rather than a silent truncation.

    <div class="member"><div class="info">
      <h4><strong>ACI BUILDING SYSTEMS, LLC</strong></h4>
      <p>10125 Highway 6 West</p>
      <p>Batesville, Mississippi 38606</p>
      <a href="tel:662-563-4574">662-563-4574</a>
"""
from __future__ import annotations
import html as _html
import re
from pathlib import Path
from ._browser import browser_get
from ._common import LayoutChanged, contract_row, require

URL = "https://mbma.com/building-systems-directory"
MEMBER = re.compile(r'<div class="member">(.*?)</div></div>', re.S)
NAME = re.compile(r"<h4><strong>(.*?)</strong></h4>", re.S)
PARA = re.compile(r"<p>(.*?)</p>", re.S)
SITE = re.compile(r'<div class="links">\s*<a href="([^"]+)"', re.S)
# "Batesville, Mississippi 38606" — city, spelled-out state, ZIP.
CITY_STATE_ZIP = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Za-z .]+?)\s+(?P<zip>\d{5})(?:-\d{4})?$")

STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}

MAX_SCROLLS = 40


def _scroll_to_the_end(page) -> None:
    """Scroll until the member count stops growing, then once more to be sure."""
    seen, stable = 0, 0
    for _ in range(MAX_SCROLLS):
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(1200)
        n = page.locator("div.member").count()
        if n == seen:
            stable += 1
            if stable >= 2:
                return
        else:
            seen, stable = n, 0
    raise LayoutChanged(
        f"the MBMA directory was still loading members after {MAX_SCROLLS} scrolls ({seen} so far). "
        "Publishing what had arrived would be a truncated roster presented as the roster.")


def _rows(page_html: str) -> list[dict]:
    out = []
    for block in MEMBER.findall(page_html):
        nm = NAME.search(block)
        if not nm:
            continue
        paras = [" ".join(_html.unescape(re.sub("<[^>]+>", " ", p)).split()) for p in PARA.findall(block)]
        paras = [p for p in paras if p]
        street, city, state, zip_code = "", "", "", ""
        for p in paras:
            m = CITY_STATE_ZIP.match(p)
            if m and m.group("state").strip() in STATES:
                city, state = m.group("city").strip(), STATES[m.group("state").strip()]
                zip_code = m.group("zip")
            elif m:
                city, state = m.group("city").strip(), ""      # Alberta, Ontario, Manitoba…
                zip_code = m.group("zip")
            elif not street:
                street = p
        site = SITE.search(block)
        out.append({"name": " ".join(_html.unescape(nm.group(1)).split()), "street": street,
                    "city": city, "state": state, "zip": zip_code,
                    "site": site.group(1) if site else ""})
    return out


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [browser_get(URL, archive_dir, "building-systems.html", timeout_ms=90000,
                        after_load=lambda page: _scroll_to_the_end(page))]


def parse(paths: list[Path], source: dict) -> list[dict]:
    page = next((p for p in paths if p.name == "building-systems.html"), None)
    require(page is not None, paths[0] if paths else Path(URL),
            "no building-systems.html in the archived folder — refresh this source before parsing")
    rows = _rows(page.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        raise LayoutChanged(
            "no div.member blocks in the rendered directory. A plain fetch of this URL returns the "
            "navigation shell and parses to zero — check the archive was made with the browser.")
    out, foreign, no_street = [], 0, 0
    for r in rows:
        if not r["state"]:
            foreign += 1          # the filter lists Alberta and Ontario beside the states
            continue
        if not r["street"]:
            no_street += 1
        out.append(contract_row(
            source, len(out) + 1, name=r["name"], address=r["street"], city=r["city"],
            state=r["state"], zip_code=r["zip"], source_url=URL, source_document=page.name,
            source_identifier=r["site"] or r["name"], website=r["site"],
            notes=f"MBMA building systems member; AC472 accredited through IAS"))
    require(bool(out), page, f"{len(rows)} members parsed and none was in a US state")
    out[0]["notes"] += (f" | {len(out)} US building-systems members of {len(rows)} listed; "
                        f"{foreign} non-US skipped; {no_street} without a street")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
