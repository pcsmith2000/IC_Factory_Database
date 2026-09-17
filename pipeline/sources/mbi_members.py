"""mbi_members — Modular Building Institute member directory.

MBI runs its directory on GrowthZone at members.modular.org. The registry records this source as
`method: browser`, and it is not: the A-Z endpoint returns the member cards in the served HTML, so
26 plain GETs get the whole directory without a headless browser. Verified 2026-09-17 against
`FindStartsWith?term=D`, 459 KB, cards marked up as schema.org/LocalBusiness.

Each card carries what this project is short of — a STREET ADDRESS — plus the one field that
mechanically separates plants from the rest of the membership:

    <span class="... gz-membership-type">Manufacturer/Direct</span>
    <h5 class="card-title gz-card-title" itemprop="name"><a ...>DaRo Structures Inc</a></h5>
    <span itemprop="streetAddress">500 S. Division St.</span>
    <span itemprop="addressLocality">Waunakee</span><span itemprop="addressRegion">WI</span>

MBI's membership is manufacturers, dealers, architects, financiers, suppliers and consultants.
Only the manufacturer types are pulled; everything else is counted and reported in the run record
rather than silently dropped, because "how much of this directory did we ignore" is the question
anyone auditing coverage will ask first.

The detail slug (`daro-structures-inc-4882563`) is stable per member and is kept as
source_identifier, so a member that renames is still the same row next quarter.

If NO letter yields a card, that is LayoutChanged: the whole directory going silently empty is
exactly the failure a quarterly run must not absorb. A single empty letter is not — measured
2026-09-17, Y has 0 members, X and Z have 1 each and Q has 2, so failing per letter would make the
fetcher break on an ordinary directory.
"""
from __future__ import annotations
import html as _html
import re
from pathlib import Path
from ._common import LayoutChanged, contract_row, http_get, require

BASE = "https://members.modular.org"
# MBI runs TWO overlapping directories and neither contains the other. The general membership
# directory, filtered to manufacturer tiers, gave 182; the dedicated manufacturer view gave 113, of
# which 16 were absent from the first — MODLOGIQ, Marengo Structures, National Modular Mfg, Resia,
# Arning Companies among them. Sweeping only one silently loses real plants, so sweep both and
# union on the detail slug.
DIRECTORIES = ("member-directory", "manufacturerdirect")
LETTER_URL = BASE + "/{directory}/FindStartsWith?term={letter}"
# Digits included because a sweep that assumes A-Z is the bug this repo found twice in one day —
# sipa published one page of five, mbma one screen of an infinite scroll. GrowthZone does answer
# ?term=0 .. ?term=9 and MBI does have members there: 33 Holdings, 360Connect, 3M ISD, 4Ward
# Solutions Group, 720 Modular.
#
# Every one of them is a non-manufacturer tier — Associate Materials, Associate Services,
# Contractor/Builder, Owner/Developer — so today this adds 20 GETs and exactly zero rows. It is in
# anyway: the cost is 20 requests, and the alternative is a roster that silently omits the first
# digit-named modular manufacturer to join. ?term=# returns the whole 724-member directory in one
# response and is NOT used, because the tier filter below is what keeps this source to plants and
# a single unfiltered fetch makes it easier to lose that.
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# One card per member. Non-greedy to the next card so a missing field cannot borrow the next
# member's address — the failure mode that would silently attach a real street to the wrong plant.
CARD = re.compile(r'<div class="card gz-directory-card.*?(?=<div class="card gz-directory-card|\Z)', re.S)
MEMBERSHIP = re.compile(r'class="[^"]*gz-membership-type[^"]*"[^>]*>(.*?)</span>', re.S)
NAME = re.compile(r'itemprop="name"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S)
# The detail slug carries a per-LISTING id, not a per-member one: A American Container is
# a-american-container-2575167 in one directory and -2575168 in the other, Autovol is
# autovol-1923229 and autovol-1960660. So the slug cannot dedupe across the two sweeps — doing
# that gave 295 rows where 198 members exist. It is still kept as source_identifier, because
# within a directory it is stable across quarters, which is what a re-run needs.
SLUG = re.compile(r'/(?:member-directory|manufacturerdirect)/Details/([A-Za-z0-9\-]+)')
DEDUPE_TRIM = re.compile(r"[^a-z0-9]")
PROP = 'itemprop="{p}"[^>]*>(.*?)</span>'

# Membership types that denote a factory. MBI sells several tiers; "Manufacturer/Direct" is the
# plant membership. Matched case-insensitively on the word so a tier rename to
# "Manufacturer/Direct Member" still lands.
MANUFACTURER = re.compile(r"manufacturer", re.I)


def _text(pattern: str, blob: str) -> str:
    m = re.search(pattern, blob, re.S)
    if not m:
        return ""
    return _html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip(" ,\xa0\t\r\n")


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    """72 GETs: 36 starting characters across each of the two directories, archived distinctly."""
    return [http_get(LETTER_URL.format(directory=d, letter=ch), archive_dir, f"{d}-{ch}.html")
            for d in DIRECTORIES for ch in LETTERS]


def parse(paths: list[Path], source: dict) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple] = set()
    skipped: dict[str, int] = {}
    position = 0
    total_cards = 0
    for path in paths:
        directory, letter = path.stem.rsplit("-", 1)
        blob = path.read_text(encoding="utf-8", errors="replace")
        cards = CARD.findall(blob)
        total_cards += len(cards)
        for card in cards:
            name = _text(NAME.pattern, card)
            if not name:
                continue
            kind = _text(MEMBERSHIP.pattern, card) or "(no membership type)"
            # A card in the dedicated manufacturer directory is a manufacturer by construction;
            # requiring the membership-type span there would drop every card that lacks one.
            if directory != "manufacturerdirect" and not MANUFACTURER.search(kind):
                skipped[kind] = skipped.get(kind, 0) + 1
                continue
            slug_m = SLUG.search(card)
            slug = slug_m.group(1) if slug_m else ""
            city = _text(PROP.format(p="addressLocality"), card)
            key = (DEDUPE_TRIM.sub("", name.lower()), DEDUPE_TRIM.sub("", city.lower()))
            if key in seen:
                continue          # the same member listed in both directories
            seen.add(key)
            position += 1
            out.append(contract_row(
                source, position, name=name,
                address=_text(PROP.format(p="streetAddress"), card),
                city=city,
                state=_text(PROP.format(p="addressRegion"), card),
                zip_code=_text(PROP.format(p="postalCode"), card),
                source_url=LETTER_URL.format(directory=directory, letter=letter),
                source_document=path.name,
                source_identifier=slug,
                notes=f"MBI membership type: {kind}"))
    if not total_cards:
        raise LayoutChanged(f"no member cards in any of {len(paths)} letter pages — the directory "
                            f"markup changed, or GrowthZone now renders the list client-side")
    require(bool(out), paths[0],
            "cards parsed but none were a manufacturer membership — check whether MBI renamed the "
            f"tier; types seen: {sorted(skipped)[:8]}")
    # Deliberately loud: a reader asking "what did this source leave out?" gets the answer without
    # opening the archive.
    out[0]["notes"] += (f" | {len(out)} manufacturer members kept, "
                        f"{sum(skipped.values())} non-manufacturer members skipped across "
                        f"{len(skipped)} membership types")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
