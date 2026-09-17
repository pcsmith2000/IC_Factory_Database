"""woodworks_mass_timber — WoodWorks' mass timber manufacturer & fabricator locations.

Mass timber was the emptiest segment in this database: ten control rows, zero reachable. No
regulator lists a CLT plant as such, and the industry is small enough that it has never shown up
as a NAICS cluster worth mining. WoodWorks publishes the roster as a dated PDF —
`mass_timber_manufacturer_locations.pdf`, the copy read here stamped January 2026 — and it is the
only public document that names the plants and says where they are.

It is a MAP, not a table. Extracting its text straight gives the two panels interleaved word by
word, and "Drain," from the right-hand panel lands inside Mercer's address block. The left-hand
panel is the alphabetical list and it reconstructs perfectly on its own, so only words starting
left of x=160 are read. That cutoff is the parse: 168.5 is where the right panel's text begins.

The left panel repeats a three-part entry:

    Mercer
    (CLT, GLT, Glulam, fabricator)
    Conway, AR; Okanagan Falls, BC;
    Spokane, WA

A line is a company NAME only when the line under it opens with "(" — which is what keeps the
page's own prose ("As a non-profit, WoodWorks...") out of the roster without hard-coding a line
count. Locations run on until the next name, semicolon-separated, and a segment may share one
state between several towns: "Drain, Portland & Swisshome, OR" is three Oregon plants.

Canadian entries are counted and skipped, not parsed into US rows — roughly a third of this
roster is BC, ON, AB and MB.

One entry is genuinely ambiguous and is taken at face value rather than guessed at. Western Forest
Products prints "Vancouver & Washougal, WA", which reads literally as two Washington towns —
Vancouver, WA is ten miles from Washougal — but the company is headquartered in Vancouver, BC, so
this may be one plant printed loosely. Both rows are emitted, because the page says both and
choosing between them would be this source inventing a fact the document does not carry. Worth a
human minute if the Vancouver, WA row ever matters.

`needs_classify: false`. A mass timber manufacturer on WoodWorks' own manufacturer roster does not
need a model's opinion on whether it manufactures.
"""
from __future__ import annotations
import collections
import re
from pathlib import Path
from ._common import LayoutChanged, contract_row, http_get, pdf_lines, require

URL = "https://www.woodworks.org/wp-content/uploads/mass_timber_manufacturer_locations.pdf"

# The right-hand map panel starts at x≈168.5. Everything left of this is the alphabetical list.
LEFT_PANEL_X = 160.0
PRODUCTS = re.compile(r"^\(.*\)$")
# "Conway, AR;" · "Drain, Portland & Swisshome, OR;" · "Sturgeon County, AB"
SEGMENT = re.compile(r"^(?P<places>.+),\s*(?P<state>[A-Z]{2})$")

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}


def _left_column(path: Path) -> list[str]:
    """The PDF's left panel, one string per printed line, in page order."""
    words = [w for line in pdf_lines(path) for w in line if w["x0"] < LEFT_PANEL_X]
    rows: dict[tuple, list] = collections.defaultdict(list)
    for w in words:
        rows[(w["page"], round(w["top"]))].append(w)
    return [" ".join(w["text"] for w in sorted(rows[k], key=lambda x: x["x0"])).strip()
            for k in sorted(rows)]


def _entries(lines: list[str]) -> list[dict]:
    """Group the left panel into {name, products, locations[]}.

    A line is a name only when the NEXT line is the parenthesised product list. That is the
    document's own structure, and it excludes the page's intro prose without counting lines or
    hard-coding where the roster starts. Everything after the product line, up to the next name,
    is that entry's locations — Mercer's run over two printed lines.
    """
    out: list[dict] = []
    cur: dict | None = None
    for i, line in enumerate(lines):
        if not line:
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not PRODUCTS.match(line) and PRODUCTS.match(nxt):
            cur = {"name": line, "products": nxt, "locations": []}
            out.append(cur)
        elif cur is not None and not PRODUCTS.match(line):
            cur["locations"].append(line)
    return out


def _places(locations: list[str]) -> list[tuple[str, str]]:
    """Semicolon-separated segments into (city, state), US only.

    A segment may share one state between several towns — "Drain, Portland & Swisshome, OR" is
    three Oregon plants — so the trailing two-letter code applies to every town before it.
    """
    out: list[tuple[str, str]] = []
    # Line by line, NOT joined. Joining appended the PDF's own filename artifact
    # ("FRA-949_MANUFACTURER_LOCATIONS_MAP_Jan2026.indd") to the last entry's location line, and
    # the segment stopped matching — Western Forest Products' Washougal plant vanished silently.
    # Each printed line's segments are self-contained, so a line that is not a location simply
    # yields nothing instead of poisoning the one above it.
    for seg in [x for line in locations for x in re.split(r";", line)]:
        seg = seg.strip().rstrip(";").strip()
        m = SEGMENT.match(seg)
        if not m:
            continue
        state = m.group("state").upper()
        if state not in US_STATES:
            continue
        for town in re.split(r",|&", m.group("places")):
            town = town.strip()
            if town:
                out.append((town, state))
    return out


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [http_get(URL, archive_dir, "mass_timber_manufacturer_locations.pdf")]


def parse(paths: list[Path], source: dict) -> list[dict]:
    path = next((p for p in paths if p.suffix.lower() == ".pdf"), None)
    require(path is not None, paths[0] if paths else Path(URL),
            "no PDF in the archived folder — refresh this source before parsing")
    entries = _entries(_left_column(path))
    if not entries:
        raise LayoutChanged(
            "no '<name>' / '(<products>)' pairs in the PDF's left panel — WoodWorks has redrawn "
            "the map, and the panel this source reads no longer exists in that shape")

    out, position, foreign_only = [], 0, 0
    for e in entries:
        places = _places(e["locations"])
        if not places:
            foreign_only += 1     # the whole entry is Canadian, or it printed no town at all
            continue
        for city, state in places:
            position += 1
            out.append(contract_row(
                source, position,
                # One row per town, but the NAME is the company alone. Layer 4's signature for a
                # row with no street is state + city + name, so "Mercer" in Conway and "Mercer" in
                # Spokane stay two facilities — the plant identity lives in the city and state
                # columns, where the rest of the pipeline looks for it. Writing the town into the
                # name instead ("Mercer — Conway, AR") cost this source every control match it
                # should have made: nothing prefix-matches "Mercer Mass Timber" once a city is
                # glued on the end.
                name=e["name"],
                address="", city=city, state=state,
                source_url=URL, source_document=path.name,
                source_identifier=f"{e['name']}|{city},{state}",
                notes=f"WoodWorks mass timber roster; products {e['products']}"))

    require(bool(out), path, "entries parsed but none named a US location")
    out[0]["notes"] += (f" | {len(out)} US plants across {len(entries) - foreign_only} companies; "
                        f"{foreign_only} of {len(entries)} companies are Canadian-only and skipped; "
                        f"no street addresses — this roster prints town and state only")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
