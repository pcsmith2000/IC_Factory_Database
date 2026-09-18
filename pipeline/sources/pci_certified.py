"""pci_certified — PCI-certified precast plants, swept state by state through the browser.

Precast concrete was the emptiest segment left: 0 of 3 control rows, and the registry entry has
sat at `queued` pointing at a directory "republished in Ascent magazine" — the magazine PDFs are
real but the newest is 2016.

The live directory is an ASP.NET/Telerik grid at
/PCIMS/Directories/Certified_Plants_Search.aspx. It answers a plain fetch, but what it serves is a
regional default: twelve plants across CO, ID, MT and UT. The roster only appears after the form
is submitted, and the form filters by state, so the sweep is one submit per state. That is what
`method: browser` was always for.

Each result row is unusually complete for a certification directory — name, street, city, state,
ZIP, phone, website, certification category, and the products the plant is certified to make:

    Olympus Precast Plant  16120 S Pony Express Rd Box 100, Bluffdale, UT 84065  United States
    (801) 571-5041  Certification Category: AC,B3,C3
    Products Produced: Architectural Precast, Beams, Box Beams/Slabs, Columns, Double Tees,
    Hollow Core Slabs, Single Tees, Sound Walls, Stadium Seats, Structural Wall Panels

NOT every PCI plant is industrialized construction. The certification covers infrastructure
precast as much as building precast: Vossloh Tie Technologies is certified for Rail Road Ties,
and the product list runs to box culverts, piles and sound walls. A plant is kept only when its
own "Products Produced" names something that goes into a BUILDING. That is the same boundary
prompt v1.2 drew for the classifier, applied here from the directory's own field rather than a
model's opinion, which is why this source carries `needs_classify: false`.

The state dropdown carries 84 options and they are not all US states — Alberta, Yukon, Western
Australia and the Armed Forces regions are in the same list, and "WA" appears twice, once for
Washington and once for Western Australia. Options are therefore filtered against the US state
set by their LABEL, not their code.
"""
from __future__ import annotations
import html as _html
import re
from pathlib import Path
from ._browser import browser_get
from ._common import US_STATES, LayoutChanged, contract_row, require

URL = "https://www.pci.org/PCIMS/Directories/Certified_Plants_Search.aspx"
_PREFIX = "ctl01_TemplateBody_WebPartManager1_gwpciNewQueryMenuCommon_ciNewQueryMenuCommon_ResultsGrid_Sheet0"
STATE_SELECT = f"#{_PREFIX}_Input3_DropDown1"
SUBMIT = f"#{_PREFIX}_SubmitButton"

ROW = re.compile(r'<tr[^>]*class="rg(?:Alt)?Row[^"]*"[^>]*>(.*?)</tr>', re.S)
CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
# "34956 Co Rd 126, Sidney, MT 59270" — street, city, state, ZIP, in one run of text.
ADDRESS = re.compile(r"(?P<street>[^,]{4,60}),\s*(?P<city>[A-Za-z .'\-]{2,28}),\s*"
                     r"(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?")
PRODUCTS = re.compile(r"Products Produced:\s*(?P<products>.+?)\s*$", re.S)
CATEGORY = re.compile(r"Certification Category:\s*(?P<cat>[A-Z0-9,]+)")

# Products that go into a BUILDING. Everything else the certification covers — rail ties, box
# culverts, pipe, piles, sound walls, utility structures — is infrastructure, and this database is
# about buildings.
BUILDING_PRODUCTS = (
    "architectural precast", "architectural trim", "beams", "box beams", "columns",
    "double tees", "hollow core", "i-beams", "girders", "joists", "single tees", "stairs",
    "structural wall panels", "wall panels", "stadium seats", "bleachers", "insulated",
)


# The dropdown mixes US states with Alberta, Yukon, Western Australia and the Armed Forces
# regions, and "WA" is the code for BOTH Washington and Western Australia — so the label decides
# which options are swept, never the code.
LABELS = {
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


def _options(page_html: str) -> list[tuple[str, str]]:
    m = re.search(r'<select[^>]*id="[^"]*Input3_DropDown1"[^>]*>(.*?)</select>', page_html, re.S)
    if not m:
        raise LayoutChanged("no state dropdown on the PCI search page — the form has changed")
    out = []
    for value, label in re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', m.group(1), re.S):
        name = " ".join(_html.unescape(label).split())
        if name in LABELS:
            out.append((value, LABELS[name]))
    return out


def _is_building_precast(products: str) -> bool:
    p = products.lower()
    return any(w in p for w in BUILDING_PRODUCTS)


def _rows(page_html: str) -> list[dict]:
    out = []
    for block in ROW.findall(page_html):
        cells = [" ".join(_html.unescape(re.sub("<[^>]+>", " ", c)).split()) for c in CELL.findall(block)]
        cells = [c for c in cells if c]
        if not cells:
            continue
        body = cells[0]
        name = cells[1] if len(cells) > 1 else body.split(",")[0]
        a = ADDRESS.search(body)
        if not a or a.group("state").upper() not in US_STATES:
            continue
        prod = PRODUCTS.search(body)
        cat = CATEGORY.search(body)
        # The street begins at its house number. The cell runs name-then-address and many names
        # carry their own comma — "Basin Precast, Inc. 34956 Co Rd 126" — so the segment before the
        # city is "Inc. 34956 Co Rd 126" and stripping the full name off the front does not help.
        # Almost every US street address opens with a number; when none is present, keep the text
        # as the directory wrote it rather than guess.
        street = a.group("street").strip()
        d = re.search(r"\d", street)
        if d:
            street = street[d.start():].strip()
        out.append({"name": name, "street": street, "city": a.group("city").strip(),
                    "state": a.group("state").upper(), "zip": a.group("zip"),
                    "products": (prod.group("products").strip() if prod else ""),
                    "category": (cat.group("cat") if cat else "")})
    return out


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    index = browser_get(URL, archive_dir, "search.html", timeout_ms=45000)
    options = _options(index.read_text(encoding="utf-8", errors="replace"))
    if not options:
        raise LayoutChanged("the PCI state dropdown carried no recognisable US state labels")
    paths = [index]
    for value, st in options:
        def pick(page, _v=value):
            page.select_option(STATE_SELECT, _v, timeout=15000)
            page.click(SUBMIT, timeout=15000)
            page.wait_for_timeout(5000)
        try:
            paths.append(browser_get(URL, archive_dir, f"state-{st}.html", timeout_ms=45000,
                                     after_load=pick))
        except Exception:
            continue          # one state that will not submit must not cost the other fifty
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    pages = sorted(p for p in paths if p.name.startswith("state-"))
    require(bool(pages), paths[0] if paths else Path(URL),
            "no per-state result pages in the archived folder — refresh this source before parsing")
    out, seen, infrastructure = [], set(), 0
    for path in pages:
        for r in _rows(path.read_text(encoding="utf-8", errors="replace")):
            key = (re.sub(r"[^a-z0-9]", "", r["street"].lower()), r["zip"])
            if key in seen:
                continue      # a plant listed under two certification categories
            seen.add(key)
            if not _is_building_precast(r["products"]):
                infrastructure += 1
                continue
            out.append(contract_row(
                source, len(out) + 1, name=r["name"], address=r["street"], city=r["city"],
                state=r["state"], zip_code=r["zip"], source_url=URL,
                source_document=path.name, source_identifier=f"{r['street']}|{r['zip']}",
                notes=f"PCI certification {r['category']}; produces {r['products']}"[:200]))
    # A state page with no rows at all is the failure this source is most likely to have, and the
    # one least likely to announce itself: the 2026-09-17 sweep archived 46 pages, 42 of them the
    # empty form at exactly 80,648 bytes, and returned 10 plants from the four states the page
    # happens to default to. It would have published "10 plants across 46 states", which is false.
    # A roster that is empty for most of the country is a broken query, not a thin industry.
    with_rows = sum(1 for path in pages
                    if _rows(path.read_text(encoding="utf-8", errors="replace")))
    if with_rows <= len(pages) // 2:
        raise LayoutChanged(
            f"only {with_rows} of {len(pages)} state pages returned any row. Selecting a state and "
            "submitting is not filtering the grid — the plants that do come back are the page's "
            "own default region (CO/ID/MT/UT), not a search result. The form needs more than the "
            "state dropdown: try setting the query type to 'Search by PCI Certified Plant "
            "Location' first, or supplying a certification category or product.")
    if not out:
        raise LayoutChanged(
            f"{len(pages)} state pages archived and none yielded a building-precast plant — either "
            "the grid markup changed or every submit returned the regional default")
    out[0]["notes"] += (f" | {len(out)} building-precast plants from {with_rows} of {len(pages)} "
                        f"state pages; {infrastructure} certified plants skipped as infrastructure "
                        "precast (rail ties, culverts, pipe, piles)")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
