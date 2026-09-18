"""superior_walls — the Superior Walls licensed precast manufacturer network.

Precast concrete panel was 0 of 3 reachable control rows, and two of the three are Superior Walls
licensees: "SUPERIOR WALLS - ADVANCED CONCRETE SYSTEMS" and "SUPERIOR WALLS - WEAVER PRECAST".
Superior Walls of America does not manufacture; a network of independent licensees does, each with
its own plant, and each with a page on superiorwalls.com.

The licensee list is not on the locator page — that renders client-side and serves 534 words of
nothing. It is in the site's WordPress REST API, `/wp-json/wp/v2/pages?per_page=100`, which lists
all 78 pages in one request. The API's `content` is raw shortcode, so the addresses are read from
the RENDERED page instead: `content` interleaves image URLs whose dimensions parse as street
numbers, which gave Advanced Concrete an address of "3955 55".

Which pages are licensees is decided by what the page contains, not by a hard-coded slug list: a
licensee page carries a "Contact Information" block with its own street address. Every page is
fetched and the ones without such a block are counted and skipped, so a licensee added to the
network next quarter is picked up without editing this file.

Two addresses appear on every page and only one of them is the plant:

    Contact Information  Superior Walls by Advanced Concrete  570-837-3955
                         55 Advanced Lane  Middleburg, PA 17842
    CORPORATE OFFICES    Superior Walls  937 East Earl Road  New Holland, PA 17557

The parse anchors on "Contact Information" and stops before "CORPORATE OFFICES" — the same trap as
bldr.com's Irving footer, where taking the first address on the page put the Albuquerque plant in
Texas. A page whose contact address IS the New Holland corporate one is treated as not a licensee.

`needs_classify: false`: a licensed manufacturer of precast foundation walls is a precast plant.
"""
from __future__ import annotations
import html as _html
import json
import re
import time
from pathlib import Path
from ._common import US_STATES, LayoutChanged, contract_row, html_text, http_get, require

BASE = "https://www.superiorwalls.com"
PAGES_API = BASE + "/wp-json/wp/v2/pages?per_page=100&_fields=slug,title,link"

# The city line, on its own: "Middleburg, PA 17842".
CITY_LINE = re.compile(r"^(?P<city>[A-Za-z .'\-]{3,28}),\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5})$")
STREET = re.compile(r"^\d[\w .'\-#]{3,48}$")
PHONE = re.compile(r"^\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}$")
CORPORATE_ZIP = "17557"          # Superior Walls of America, New Holland PA — not a plant
PACE_SECONDS = 0.4


def _lines(page_html: str) -> list[str]:
    """The page as printed LINES, not as one flattened string.

    Flattening loses the <br> between street and city, and "55 Advanced Lane Middleburg" has no
    delimiter left to split on — the first attempt read the street as "55 Advanced" and the town as
    "Lane Middleburg". The line breaks are the only thing separating those two fields.
    """
    broken = re.sub(r"<(?:br\s*/?|/p|/div|/li|/h[1-6])>", "\n", page_html, flags=re.I)
    return [" ".join(x.split()) for x in html_text(broken).splitlines() if x.strip()]


def _licensee(page_html: str) -> dict | None:
    """The plant on this page, or None when the page is not a licensee's."""
    lines = _lines(page_html)
    # Cut at CORPORATE OFFICES so the New Holland address can never be reached at all.
    for i, x in enumerate(lines):
        if "CORPORATE OFFICES" in x.upper():
            lines = lines[:i]
            break
    try:
        start = next(i for i, x in enumerate(lines) if "contact information" in x.lower())
    except StopIteration:
        return None
    block = lines[start:start + 12]
    city_at = next((i for i, x in enumerate(block) if CITY_LINE.match(x)), None)
    if city_at is None:
        return None
    cm = CITY_LINE.match(block[city_at])
    state, zip_code = cm.group("state").upper(), cm.group("zip")
    if state not in US_STATES or zip_code == CORPORATE_ZIP:
        return None
    street = next((block[i] for i in range(city_at - 1, 0, -1) if STREET.match(block[i])), "")
    # The name is the first line after the heading that is neither a phone number nor the street.
    name = ""
    for x in block[1:city_at]:
        if PHONE.match(x) or STREET.match(x) or not x:
            continue
        name = x.replace("Contact Information", "").strip()
        if name:
            break
    if not name:
        return None
    return {"name": _html.unescape(name), "street": _html.unescape(street),
            "city": _html.unescape(cm.group("city")).strip(), "state": state, "zip": zip_code}


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    index = http_get(PAGES_API, archive_dir, "pages.json")
    try:
        pages = json.loads(index.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise LayoutChanged(f"wp-json did not return JSON ({e}); the REST API may be disabled") from e
    if not pages:
        raise LayoutChanged("wp-json returned no pages — the REST API is reachable but empty")
    paths, gone = [index], []
    for p in pages:
        slug, link = (p.get("slug") or ""), (p.get("link") or "")
        # Skip the obvious non-pages before spending a request on them. Everything else is fetched
        # and judged on its contents, so a new licensee needs no edit here.
        if not slug or not link or slug.startswith("form-thank-you") or slug.endswith("-test-page"):
            continue
        try:
            # The API's `link` is canonical. Building BASE/slug/ instead 404s on child pages, whose
            # real URL is nested under a parent.
            paths.append(http_get(link, archive_dir, f"{slug}.html"))
        except Exception as e:                     # one dead page must not cost the whole roster
            gone.append(f"{slug}: {type(e).__name__}")
        time.sleep(PACE_SECONDS)
    if gone:
        (archive_dir / "unfetchable.json").write_text(json.dumps(gone, indent=1))
    if len(gone) > len(pages) // 2:
        raise LayoutChanged(f"{len(gone)} of {len(pages)} pages could not be fetched — "
                            "superiorwalls.com has moved or gated its pages")
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    pages = [p for p in paths if p.suffix.lower() == ".html"]
    require(bool(pages), paths[0] if paths else Path(PAGES_API),
            "no page HTML in the archived folder — refresh this source before parsing")
    out, skipped, seen, repeats = [], 0, set(), 0
    for path in sorted(pages):
        lic = _licensee(path.read_text(encoding="utf-8", errors="replace"))
        if not lic:
            skipped += 1
            continue
        # One plant, two pages. Warrior Precast is both /superior-walls-warrior-precast/ and
        # /superior-walls-east-tennessee/; Weaver and Northeast each have a second slug too. The
        # street and ZIP are the plant, so that is the key — not the slug, which is the page.
        key = (re.sub(r"[^a-z0-9]", "", lic["street"].lower()), lic["zip"])
        if key in seen:
            repeats += 1
            continue
        seen.add(key)
        out.append(contract_row(
            source, len(out) + 1, name=lic["name"], address=lic["street"],
            city=lic["city"], state=lic["state"], zip_code=lic["zip"],
            source_url=f"{BASE}/{path.stem}/", source_document=path.name,
            source_identifier=path.stem,
            notes="Superior Walls licensed precast manufacturer"))
    if not out:
        raise LayoutChanged(
            f"none of {len(pages)} pages carried a 'Contact Information' block with a plant "
            "address — superiorwalls.com has changed its licensee page template")
    out[0]["notes"] += (f" | {len(out)} licensee plants from {len(pages)} pages; {repeats} pages "
                        f"were a second slug for a plant already seen; {skipped} carried no "
                        "licensee contact block (products, corporate, news)")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
