"""bldr_locations — Builders FirstSource manufacturing plants, from its own location index.

Builders FirstSource is the largest single gap this database has: 21 rows of the ADL control list
are BFS plants in 13 states, and until now the warehouse held almost none of them. The company was
already configured under `corporate_locations`, whose model extraction produced zero rows from
bldr.com because the index page carries no addresses — only links.

Those links are the data. `/location/all-locations` serves 589 of them in the HTML, each of the
shape `/location/<city>-<st>-<kind>/<BRANCHCODE>`, and the branch code's last two characters say
what the site is:

    YD  357  lumber yard          MF   90  MANUFACTURING (truss, wall panel, components)
    MW   71  millwork             WN    8  windows          SR  7  showroom
    HC   12  home center          DC    5  distribution     CS  4  customer service

Only `MF` is pulled. A lumber yard is a distribution point, not a plant, and millwork is doors and
mouldings — the classifier prompt has excluded that family since v1.2. The other 499 are counted
and reported rather than silently dropped.

The branch code (`ACWOGAMF`) is a stable per-facility key and is kept as source_identifier, which
is what lets a plant survive a rename and join across sources.

Street, city and state all come from ONE anchor — the Google Maps link the page builds for its own
location block:

    <a href="https://www.google.com/maps/place/4255 McEver Industrial Dr.,Acworth,GA,30101/"
       class="placeLink">

Nothing else on the page is safe. Every page's footer carries the Irving, TX corporate address, so
taking the first street-shaped string in the HTML gave Albuquerque's plant an address in Texas.
The footer has no placeLink, which is what makes the anchor worth having.

The page title ("Acworth GA Truss | Builders FirstSource") is the cross-check, not the source: it
sometimes leads with a subsidiary brand instead of a place — "Spenard Builders Supply Eklutna AK"
puts the town third — and "Coastal Carolina Truss" names no town at all. So the map link decides
city and state, the title supplies the site kind, and a street is kept only where the two agree on
the state. A page rendering some other branch's block costs this row its street rather than
handing it somebody else's.

Measured 2026-09-17: 90 plants, 89 with a street, 90 located to a city and state.
"""
from __future__ import annotations
import html as _html
import re
import time
from pathlib import Path
from ._common import LayoutChanged, contract_row, http_get, require

BASE = "https://www.bldr.com"
INDEX = BASE + "/location/all-locations"

LINK = re.compile(r'href="(/location/([^"/]+)/([A-Z0-9]+))"')
# The location block's own map link. Four fields, comma-separated, inside the href — which is why
# it is trustworthy: the footer's Irving address is plain text and has no placeLink.
PLACE = re.compile(r'maps/place/([^,"/]+),([^,"/]+),([A-Z]{2}),(\d{5})[^"]*"[^>]*class="placeLink"')
TITLE = re.compile(r"<title>\s*(.*?)\s*</title>", re.S)
# "Acworth GA Truss | Builders FirstSource" — city words, two-letter state, then the kind.
TITLE_PARTS = re.compile(r"^(.+?)\s+([A-Z]{2})\s+(.*?)\s*\|", re.S)

MANUFACTURING = "MF"
PACE_SECONDS = 0.4


def _mf_links(index_html: str) -> list[tuple[str, str]]:
    """Every distinct manufacturing location on the index, as (slug, branch_code)."""
    seen, out = set(), []
    for _full, slug, code in LINK.findall(index_html):
        if not code.endswith(MANUFACTURING) or code in seen:
            continue
        seen.add(code)
        out.append((slug, code))
    return sorted(out, key=lambda x: x[1])


def _kind_counts(index_html: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _full, _slug, code in set(LINK.findall(index_html)):
        counts[code[-2:]] = counts.get(code[-2:], 0) + 1
    return counts


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    index = http_get(INDEX, archive_dir, "all-locations.html")
    links = _mf_links(index.read_text(encoding="utf-8", errors="replace"))
    if not links:
        raise LayoutChanged(
            "no /location/<slug>/<CODE ending MF> links on the index — bldr.com either renamed its "
            "branch codes or now renders the location list client-side")
    paths = [index]
    for slug, code in links:
        paths.append(http_get(f"{BASE}/location/{slug}/{code}", archive_dir, f"{code}.html"))
        time.sleep(PACE_SECONDS)
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    index = next((p for p in paths if p.name == "all-locations.html"), None)
    require(index is not None, paths[0] if paths else Path(INDEX),
            "the archived folder has no all-locations.html — refresh this source before parsing")
    kinds = _kind_counts(index.read_text(encoding="utf-8", errors="replace"))

    out, position, no_street, unlocated = [], 0, 0, 0
    for path in sorted(p for p in paths if p.name != "all-locations.html"):
        code = path.stem
        h = path.read_text(encoding="utf-8", errors="replace")
        t = TITLE.search(h)
        parts = TITLE_PARTS.match(_html.unescape(t.group(1))) if t else None
        t_city, t_state, kind = (x.strip() for x in parts.groups()) if parts else ("", "", "")
        m = PLACE.search(h)
        p_street, p_city, p_state = (_html.unescape(x).strip() for x in m.groups()[:3]) if m else ("", "", "")

        # The map link wins on city and state: it belongs to the location block, and the title
        # sometimes carries a subsidiary brand instead of a place — "Spenard Builders Supply
        # Eklutna AK" puts the town third and the brand first. The title is the cross-check.
        city, state = (p_city or t_city), (p_state or t_state)
        # A street is kept only where the two agree, or where the title had no state to disagree
        # with. Every page's FOOTER carries the Irving, TX corporate address, and it has no
        # placeLink — which is the whole reason this anchors on one. A page rendering a different
        # branch's block should cost this row its street, not hand it somebody else's.
        street = p_street if p_street and (not t_state or t_state.upper() == p_state.upper()) else ""
        if not (city and state):
            # Keep it. "Coastal Carolina Truss" names no town in its title and renders its address
            # client-side, so it has neither — but it is a real plant on a list of real plants, and
            # a row dropped here is a plant this database then claims does not exist.
            unlocated += 1
        if not street:
            no_street += 1
        position += 1
        out.append(contract_row(
            source, position,
            name=" ".join(f"Builders FirstSource — {city} {state} {kind}".split()),
            address=street, city=city, state=state.upper(),
            source_url=f"{BASE}/location/{path.stem}",
            source_document=path.name,
            source_identifier=code,
            notes=f"BFS branch type {code[-2:]} (manufacturing); site kind on page: {kind}"))

    require(bool(out), index, "manufacturing pages archived but none yielded a row")
    skipped = sum(v for k, v in kinds.items() if k != MANUFACTURING)
    out[0]["notes"] += (
        f" | {len(out)} manufacturing plants kept, {skipped} non-manufacturing locations skipped "
        f"({', '.join(f'{k}:{v}' for k, v in sorted(kinds.items()) if k != MANUFACTURING)}); "
        f"{no_street} of {len(out)} have no street address on the served page; "
        f"{unlocated} have no city or state anywhere on it")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
