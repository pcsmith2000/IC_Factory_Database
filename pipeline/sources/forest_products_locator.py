"""forest_products_locator — SREF Secondary Forest Products Locator, the Southern states' wood
manufacturer roster, read one state page at a time and filtered by name before the classifier.

Found 2026-09-17 while enriching Specialized Structures (Willacoochee GA). The site is run by the
Southern Regional Extension Forestry and lists every secondary wood manufacturer it knows in the
South — cabinets, pallets, tree services, paper mills — and, inside that, the wood truss and
building-components plants the control list is short of: a third of the control is structural
components and half the still-missing rows are in these thirteen states. Georgia alone lists
Quality-Bilt Trusses, Trussway Manufacturing, Georgia Mountain Components, Gilmer Building
Components, Mid-South Truss and North Georgia Truss Co. — the last of those is a control row no
other source held.

Three things the probe settled, and the shape of this source follows from them:

  * The site's own text search (SearchableText=truss) answers with a server error page, and
    /manufacturers with no state answers with zero cards. `getState=XX` is the ONE working scope:
    one page per state, about a megabyte, Georgia 1,273 cards.
  * The listing page itself carries a street and "City, ST ZIP" on every card. Nothing has to be
    fetched per company to place a plant — the per-company page adds Products/Size/Web, and that
    is enrichment for later, not acquisition.
  * The listing carries NO product tags, so it cannot be scoped by product. 95% of a state page is
    not IC and sending 1,300 cards a state to the classifier is the wrong shape. A card is kept
    when its NAME says what it makes — truss, components, building systems, panel, log home,
    modular, timber frame, post frame, prefab — and the kept rows go to the classifier with
    `classify_all`, since a registry row carries no NAICS for the candidate filter to see. The
    name filter is a recall floor stated in the registry, not a hidden one: a truss plant named
    "R. C. Peeples, Inc." is not found here and that is the source's declared limit.

Only the thirteen SREF states are fetched. The site accepts any state code, but a page for Ohio
would be a page of nothing, and a snapshot is judged complete by these thirteen.
"""
from __future__ import annotations
import html as _html
import re
import time
import urllib.error
from pathlib import Path
from ._common import LayoutChanged, contract_row, http_get, require

BASE = "https://secondary.forestproductslocator.org"
LIST = BASE + "/manufacturers?getState={state}&submitSearch=Search&form.submitted=1"

# SREF's own thirteen. Anything else is an empty page.
STATES = ["AL", "AR", "FL", "GA", "KY", "LA", "MS", "NC", "OK", "SC", "TN", "TX", "VA"]

# One card: the title paragraph, then the address paragraph.
CARD_SPLIT = '<p class="millTitle">'
# Two link shapes on the same listing: /manufacturers/<slug> for secondary manufacturers and
# /mill-list/<slug> for the primary mills (pulp, paper, sawmills) the site lists beside them in
# Alabama, Texas and Arkansas. Both are cards; the keep filter and the classifier sort them.
NAME = re.compile(r'href="([^"]*/(?:manufacturers|mill-list)/([a-z0-9.\-]+))"[^>]*>(.*?)</a>', re.S)
ADDR = re.compile(r"<p>\s*<span>(.*?)</span>", re.S)
# ", AL" with no city and no ZIP is how a mill card reads; the ZIP is optional and so is the city.
CITY_ST_ZIP = re.compile(r"^(?P<city>.*?),\s*(?P<state>[A-Za-z]{2})(?:\s+(?P<zip>\d{5})(?:-\d{4})?)?\s*$")

# What the NAME must say for the card to be a candidate. The classifier decides after this.
KEEP = re.compile(
    r"\btruss|\bcomponents?\b|building systems?|\bpanel|log homes?|\bmodular|timber ?frame|"
    r"post ?frame|\bprefab|\bsips?\b|structural|\bframing\b|wall systems?|roof systems?|"
    r"manufactured hom|mobile hom|tiny hom|\bcabins?\b|\bshed|\bbarn", re.I)

PACE_SECONDS = 1.0


def _cards(page: str) -> list[dict]:
    out = []
    for chunk in page.split(CARD_SPLIT)[1:]:
        m = NAME.search(chunk)
        if not m:
            continue
        name = _html.unescape(re.sub(r"<[^>]+>", "", m.group(3))).strip()
        street, city, state, zip_code = "", "", "", ""
        a = ADDR.search(chunk)
        if a:
            lines = [_html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
                     for x in re.split(r"<br\s*/?>", a.group(1))]
            lines = [x for x in lines if x]
            if lines:
                csz = CITY_ST_ZIP.match(lines[-1])
                if csz:
                    city, state, zip_code = csz.group("city").strip(), csz.group("state").upper(), csz.group("zip") or ""
                    street = " ".join(lines[:-1]).strip()
                else:
                    street = " ".join(lines).strip()
        out.append({"slug": m.group(2), "name": name, "street": street, "city": city,
                    "state": state, "zip": zip_code, "url": m.group(1)})
    return out


# The site answers a valid state with "Resource not found" (HTTP 404, an 859-byte error page)
# at random: FL, VA and NC each did it once and served 360-690 KB on the next request. http_get
# rightly refuses to retry a 4xx, so this source retries the 404 itself and, when a state stays
# down, leaves the page out and lets parse() say PARTIAL rather than lose every state to one.
TRANSIENT_RETRIES = 4


def _get_state(st: str, archive_dir: Path) -> Path | None:
    for attempt in range(TRANSIENT_RETRIES + 1):
        try:
            return http_get(LIST.format(state=st), archive_dir, f"{st}.html", timeout=300)
        except urllib.error.HTTPError as e:
            if e.code != 404 or attempt == TRANSIENT_RETRIES:
                if e.code == 404:
                    return None
                raise
            time.sleep(2 * (attempt + 1))
    return None


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    paths = []
    for st in STATES:
        p = _get_state(st, archive_dir)
        if p is not None:
            paths.append(p)
        time.sleep(PACE_SECONDS)
    if not any(_cards(p.read_text(encoding="utf-8", errors="replace")) for p in paths):
        raise LayoutChanged("no manufacturer cards on any state page — the listing markup "
                            "(p.millTitle) changed or getState no longer scopes the list")
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    pages = {p.stem.upper(): p for p in paths if p.suffix == ".html" and p.stem.upper() in STATES}
    require(bool(pages), paths[0] if paths else Path(BASE),
            "no <ST>.html state pages in the archived folder — refresh this source before parsing")
    missing = [s for s in STATES if s not in pages]

    out, seen, total, per_state = [], set(), 0, {}
    for st in STATES:
        p = pages.get(st)
        if p is None:
            continue
        cards = _cards(p.read_text(encoding="utf-8", errors="replace"))
        total += len(cards)
        kept = 0
        for c in cards:
            if not KEEP.search(c["name"]) or c["slug"] in seen:
                continue
            seen.add(c["slug"]); kept += 1
            out.append(contract_row(
                source, len(out) + 1, name=c["name"], address=c["street"], city=c["city"],
                state=c["state"] or st, zip_code=c["zip"], source_url=c["url"],
                source_document=p.name, source_identifier=c["slug"],
                notes=f"FPL {st} listing; kept because the name says what it makes"))
        per_state[st] = (len(cards), kept)

    require(bool(out), next(iter(pages.values())),
            "state pages parsed but no card name matched the keep filter — either the markup "
            "changed or the pages are empty")
    out[0]["notes"] += (f" | {len(out)} kept of {total} cards across {len(pages)} state page(s): "
                        + ", ".join(f"{s} {n}/{t}" for s, (t, n) in per_state.items()))
    if missing:
        out[0]["notes"] += f" | PARTIAL: state pages missing from this snapshot: {missing}"
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
