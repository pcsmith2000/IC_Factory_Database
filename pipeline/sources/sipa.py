"""sipa — Structural Insulated Panel Association manufacturing members.

SIP and ICF plants are 23 rows of the ADL control list and the pipeline held almost none of them.
Several are not missing from the raw data at all — ACME Panel, Enercept and Energy Panel Structures
are all in EPA FRS — they are being classified NOT-IC and dropped at Layer 3. A curated
manufacturer roster fixes both halves of that at once: it adds the plants the regulators never
list, and because SIPA's manufacturing membership IS a list of panel plants, the source carries
`needs_classify: false` and the rows do not go past the classifier to be thrown away again.

SIPA separates Manufacturing from Builder, Dealer/Distributor, Design Professional and Associate
members, and only the manufacturing page is read here.

That page is PAGINATED and the first version of this source did not notice. It read
/members/manufacturing, found ten cards, and the docstring above this line used to say "ten
manufacturers, all US. That is the whole segment, not a sample of it" — which was false, asserted
confidently, and never checked. The index carries its own pager, /members/manufacturing/page/0
through /page/3, and page 0 alone links members the crawl never fetched: Insulspan, Panelwrights,
The Murus Company, and PorterSIPs — which is a row on the ADL control list that was being counted
as a plant no source holds.

So every page of the pager is read and the cards are unioned by slug. The page count is taken from
the index's own pager links rather than guessed, and a snapshot whose folder is missing a page the
index advertises says so in its notes instead of quietly returning a tenth of the roster.

Two documents per member, and both are needed:

  /members/manufacturing      the card list — name, member types, "City, ST"
  /members/<slug>             the profile — the full postal address in one <p>:
                              <p><strong>NAME</strong><br/>STREET<br/>CITY, ST ZIP<br/>COUNTRY</p>

The profile's address block is the only place a street exists, and it is anchored on the member's
own name in the same paragraph. A profile whose name does not match the card it came from is
refused rather than merged: the sidebar on these pages also renders projects and sponsors, and a
street taken from the wrong block is worse than no street.

A member whose profile cannot be read keeps its card — name, city and state are already enough to
locate a plant here, and dropping it would mean the database claims a SIPA manufacturer does not
exist.
"""
from __future__ import annotations
import html as _html
import re
import time
from pathlib import Path
from ._common import LayoutChanged, contract_row, http_get, require
from ..reconcile import norm_name

BASE = "https://www.sips.org"
INDEX = BASE + "/members/manufacturing"

# One card per member: the bold profile link, then everything up to the next one.
CARD = re.compile(
    r'<a href="/members/([a-z0-9-]+)"[^>]*class="font-weight-bold"[^>]*>(.*?)</a>(.*?)'
    r'(?=<a href="/members/[a-z0-9-]+"[^>]*class="font-weight-bold"|<footer)', re.S)
TYPES = re.compile(r"<strong>(.*?)</strong>", re.S)
LOC = re.compile(r"<small>(.*?)</small>", re.S)
CITY_STATE = re.compile(r"^(.*?),\s*([A-Za-z]{2})$")
# The profile's address paragraph, headed by the member's own name.
ADDRESS = re.compile(
    r"<p><strong>(?P<name>[^<]+)</strong><br\s*/?>"
    r"(?P<street>[^<]+)<br\s*/?>"
    r"(?P<city>[^<,]+),\s*(?P<state>[A-Za-z]{2})\s*(?P<zip>\d{5})?[^<]*<br\s*/?>"
    r"(?P<country>[^<]*)</p>", re.S)

# The index's own pager: /members/manufacturing/page/0 .. /page/N.
PAGE_LINK = re.compile(r'href="/members/manufacturing/page/(\d+)"')

PACE_SECONDS = 0.4


def _index_pages(paths: list[Path]) -> list[Path]:
    """Every archived index page, page 0 first."""
    return sorted((p for p in paths if p.name.startswith("manufacturing") and p.suffix == ".html"),
                  key=lambda p: (p.name != "manufacturing.html", p.name))


def _all_cards(index_paths: list[Path]) -> list[dict]:
    """Cards from every index page, unioned by slug — the pager repeats page 0 as /page/0."""
    out, seen = [], set()
    for path in index_paths:
        for c in _cards(path.read_text(encoding="utf-8", errors="replace")):
            if c["slug"] not in seen:
                seen.add(c["slug"]); out.append(c)
    return out


def _cards(index_html: str) -> list[dict]:
    out = []
    for m in CARD.finditer(index_html):
        slug, name, rest = m.group(1), _html.unescape(m.group(2)).strip(), m.group(3)
        locs = LOC.findall(rest)
        city, state = "", ""
        if locs:
            cs = CITY_STATE.match(_html.unescape(locs[-1]).strip())
            if cs:
                city, state = cs.group(1).strip(), cs.group(2).upper()
        types = TYPES.search(rest)
        out.append({"slug": slug, "name": name, "city": city, "state": state,
                    "types": _html.unescape(types.group(1)).strip() if types else ""})
    return out


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    index = http_get(INDEX, archive_dir, "manufacturing.html")
    first = index.read_text(encoding="utf-8", errors="replace")
    paths = [index]
    for n in sorted({int(x) for x in PAGE_LINK.findall(first)}):
        paths.append(http_get(f"{INDEX}/page/{n}", archive_dir, f"manufacturing-page-{n}.html"))
        time.sleep(PACE_SECONDS)
    cards = _all_cards(_index_pages(paths))
    if not cards:
        raise LayoutChanged(
            "no member cards on /members/manufacturing — SIPA either changed the card markup or "
            "moved the manufacturer list behind the member login")
    for c in cards:
        paths.append(http_get(f"{BASE}/members/{c['slug']}", archive_dir, f"{c['slug']}.html"))
        time.sleep(PACE_SECONDS)
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    indexes = _index_pages(paths)
    require(bool(indexes), paths[0] if paths else Path(INDEX),
            "the archived folder has no manufacturing.html — refresh this source before parsing")
    index = indexes[0]
    cards = _all_cards(indexes)
    require(bool(cards), index, "manufacturing.html archived but no member cards parsed from it")
    # A folder holding fewer pages than the index advertises is a partial crawl, and returning its
    # tenth of the roster as the roster is exactly what this source did for its first three weeks.
    advertised = {int(x) for x in PAGE_LINK.findall(index.read_text(encoding="utf-8", errors="replace"))}
    missing = sorted(advertised - {int(p.name.split("-")[-1][:-5]) for p in indexes
                                   if p.name.startswith("manufacturing-page-")})
    profiles = {p.stem: p for p in paths if p not in set(indexes)}

    out, no_street, foreign = [], 0, 0
    for position, c in enumerate(cards, 1):
        street, zip_code = "", ""
        prof = profiles.get(c["slug"])
        if prof is not None:
            # EVERY address paragraph, not the first: these sidebars render projects and sponsors
            # in the same shape, and on a profile where a project block comes first, taking the
            # first match cost the member its real street. The paragraph headed by THIS member's
            # name is the one that counts; a street lifted from the wrong block is worse than none.
            m = next((x for x in ADDRESS.finditer(prof.read_text(encoding="utf-8", errors="replace"))
                      if norm_name(_html.unescape(x.group("name"))) == norm_name(c["name"])), None)
            if m:
                if (m.group("country") or "").strip().lower() in ("", "united states", "usa", "us"):
                    street = _html.unescape(m.group("street")).strip()
                    zip_code = (m.group("zip") or "").strip()
                else:
                    foreign += 1
                    continue
        if not street:
            no_street += 1
        out.append(contract_row(
            source, position, name=c["name"], address=street, city=c["city"], state=c["state"],
            zip_code=zip_code,
            source_url=f"{BASE}/members/{c['slug']}",
            source_document=(prof.name if prof is not None else index.name),
            source_identifier=c["slug"],
            notes=f"SIPA member types: {c['types']}"))

    require(bool(out), index, "member cards parsed but every one was foreign or unreadable")
    out[0]["notes"] += (f" | {len(out)} SIPA manufacturing members kept from {len(indexes)} index "
                        f"page(s), {foreign} non-US skipped; {no_street} of {len(out)} have no "
                        f"street on their profile")
    if missing:
        out[0]["notes"] += (f" | PARTIAL: the index advertises pages {missing} that are not in this "
                            f"snapshot — re-fetch sipa, this is a fraction of the roster")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
