"""in_dhs — Indiana DHS industrialized building systems manufacturer registry.

The registry entry for this source has said the same thing for months: "Densest modular corridor
in the country; IN ranks 3rd in facilities without being a source yet." It was marked
`method: browser` and left queued. It is not a browser job — the Struts app behind
oas.dhs.in.gov/dfbs/code/main.do serves the whole roster in plain HTML.

It is also not an Indiana list. Indiana registers every manufacturer whose industrialized
buildings are sold into the state, so the roster spans 307 manufacturer cities across 46 states:
a national register of modular plants, kept by a regulator, that happens to live on an Indiana
server. That makes it class A with `needs_classify: false` — a plant on a state's industrialized
building systems register is an industrialized building systems plant, and it does not need a
model's opinion on the point.

Swept by letter rather than by state: 26-ish requests instead of 46, and the letter links carry
their own counts ("21 owners") so a letter that silently empties is visible. The letters are read
off the page, not hard-coded, because the roster has a lowercase 'n' bucket holding one
manufacturer whose name begins with a lowercase letter.

Each listing is a name, a street, and a city line:

    <div class="listing">
      <h3><a href="...method=view&manufacturerNameId=630">American Modular Technologies</a></h3>
      <p class="listingInfo">6306 Old Hwy 421 North<br />Liberty, NC 27298<br /></p>
    </div>

Some carry a PO Box between street and city ("3549 Highway 16 North / P O Box 428 / Denver, NC
28037"). The city line is found from the END — it is the last line shaped "City, ST ZIP" — and
everything above it is the street, so a box number cannot be mistaken for the town.

Two things the register does that would otherwise reach the warehouse intact. It keeps tombstones
in place instead of deleting rows — one listing is a manufacturer named "DELETE" at a street
address of "DELETE" — and it serves Windows-1252, so "Mécanitec" decoded as UTF-8 became mojibake
in the company's own name. Both are handled and counted.

Canadian and other non-US plants are on this register too. They are recognised by what they are
NOT: a listing with no line whose two-letter token is a US state code. "Hamilton, ON" fails that
test and "Marco Island, FL" passes it, with no second pattern for postal codes to keep in sync.

`manufacturerNameId` is stable per manufacturer and is kept as source_identifier: a plant that
renames is still the same row next quarter.
"""
from __future__ import annotations
import html as _html
import re
import time
from pathlib import Path
from ._common import US_STATES, LayoutChanged, contract_row, http_get, require

BASE = "https://oas.dhs.in.gov/dfbs/code"
INDEX = BASE + "/main.do"
LETTER_URL = BASE + "/main.do?method=filter&filter=byLetter&letter={letter}"

# The app puts a ;jsessionid=... in every self-link; it is not part of the identity of the page.
LETTER_LINK = re.compile(r'href="[^"]*?method=filter&(?:amp;)?filter=byLetter&(?:amp;)?letter=([A-Za-z])"')
LISTING = re.compile(
    r'<div class="listing">\s*<h3[^>]*>\s*<a href="[^"]*?manufacturerNameId=(?P<id>\d+)"[^>]*>'
    r'(?P<name>.*?)</a>\s*</h3>\s*<p class="listingInfo">(?P<info>.*?)</p>', re.S)
# The ZIP is OPTIONAL and is not validated. Requiring a clean 5-digit ZIP threw away six real US
# plants: "Marco Island, FL" and "Bristol, IN" carry none at all, "Hitchcock, TX 775636" has six
# digits and "Aubrey, TX 762278030" has nine unhyphenated. Those are typos in a state register, not
# evidence that the row is not a plant, and the ZIP is kept exactly as filed.
CITY_LINE = re.compile(r"^(?P<city>.+?)[;,]\s*(?P<state>[A-Za-z]{2})\.?(?:\s+(?P<zip>\d{5,9})(?:-\d{4})?)?$")
# The register keeps tombstones in place rather than removing rows. "DELETE" is a manufacturer
# name and a street address on the same listing; published, it becomes a facility called DELETE.
TOMBSTONE = re.compile(r"^(delete|deleted|do not use|test|xxx+)$", re.I)

# The app serves Windows-1252, not UTF-8: "Mécanitec" arrives as the byte 0xE9 and decoding it as
# UTF-8 with replacement turned the company's own name into mojibake in the warehouse.
ENCODING = "cp1252"


def _letters(index_html: str) -> list[str]:
    seen, out = set(), []
    for x in LETTER_LINK.findall(index_html):
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def _listings(page_html: str) -> list[dict]:
    out = []
    for m in LISTING.finditer(page_html):
        lines = [" ".join(_html.unescape(re.sub(r"<[^>]+>", " ", x)).split())
                 for x in re.split(r"<br\s*/?>", m.group("info"))]
        lines = [x for x in lines if x]
        city = state = zip_code = ""
        street_lines = lines
        # From the END: the city line is the last one shaped "City, ST ZIP". Everything above it
        # is the street, which is what keeps a PO Box line out of the town name.
        for i in range(len(lines) - 1, -1, -1):
            cm = CITY_LINE.match(lines[i])
            # The two-letter token must be a US STATE, which is what separates "Marco Island, FL"
            # from "Hamilton, ON" without needing a second pattern for Canadian postal codes.
            if cm and cm.group("state").upper() in US_STATES:
                city = cm.group("city").strip(" ;,")
                state, zip_code = cm.group("state").upper(), (cm.group("zip") or "")
                street_lines = lines[:i]
                break
        # Nothing on the listing names a US state, so it is not a US plant: Nepean ON, Welland ONT,
        # Trois-Rivières, Grimsby ON, Hamilton ON, Wingham On, and one address in Sweden.
        foreign = not city
        out.append({"id": m.group("id"), "foreign": foreign,
                    "name": " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", m.group("name"))).split()),
                    "street": street_lines[0] if street_lines else "",
                    "extra": "; ".join(street_lines[1:]),
                    "city": city, "state": state, "zip": zip_code})
    return out


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    index = http_get(INDEX, archive_dir, "index.html")
    letters = _letters(index.read_text(encoding=ENCODING, errors="replace"))
    if not letters:
        raise LayoutChanged(
            "no byLetter filter links on main.do — the DFBS app has changed its navigation, and "
            "the letter sweep this source is built on no longer exists")
    paths = [index]
    for letter in letters:
        paths.append(http_get(LETTER_URL.format(letter=letter), archive_dir, f"letter-{letter}.html"))
        time.sleep(0.4)
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    letters = sorted(p for p in paths if p.name.startswith("letter-"))
    require(bool(letters), paths[0] if paths else Path(INDEX),
            "no letter pages in the archived folder — refresh this source before parsing")

    out, seen, total, no_city, foreign, tombstones = [], set(), 0, 0, 0, 0
    for path in letters:
        letter = path.stem.split("-", 1)[1]
        for r in _listings(path.read_text(encoding=ENCODING, errors="replace")):
            total += 1
            if r["id"] in seen:
                continue                     # a manufacturer filed under two spellings of a letter
            seen.add(r["id"])
            if TOMBSTONE.match(r["name"].strip()):
                tombstones += 1; continue
            if r["foreign"]:
                foreign += 1; continue
            if not (r["city"] and r["state"]):
                no_city += 1
            note = f"Indiana DFBS industrialized building systems register; letter {letter}"
            if r["extra"]:
                note += f"; also on record: {r['extra']}"
            out.append(contract_row(
                source, len(out) + 1, name=r["name"], address=r["street"],
                city=r["city"], state=r["state"], zip_code=r["zip"],
                source_url=LETTER_URL.format(letter=letter),
                source_document=path.name, source_identifier=r["id"],
                notes=note[:200]))

    if not total:
        raise LayoutChanged(
            f"no <div class=\"listing\"> blocks across {len(letters)} letter pages — the DFBS "
            "listing markup changed, or the roster is now rendered client-side")
    out[0]["notes"] += (f" | {len(out)} US manufacturers across {len(letters)} letters; "
                        f"{total - len(out) - foreign - tombstones} duplicate listings folded on "
                        f"manufacturerNameId; {foreign} non-US skipped; {tombstones} tombstone rows "
                        f"skipped; {no_city} kept with no parseable city line")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
