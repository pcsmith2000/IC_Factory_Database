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
LETTER_URL = BASE + "/member-directory/FindStartsWith?term={letter}"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# One card per member. Non-greedy to the next card so a missing field cannot borrow the next
# member's address — the failure mode that would silently attach a real street to the wrong plant.
CARD = re.compile(r'<div class="card gz-directory-card.*?(?=<div class="card gz-directory-card|\Z)', re.S)
MEMBERSHIP = re.compile(r'class="[^"]*gz-membership-type[^"]*"[^>]*>(.*?)</span>', re.S)
NAME = re.compile(r'itemprop="name"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S)
SLUG = re.compile(r'/member-directory/Details/([A-Za-z0-9\-]+)')
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
    """26 GETs, one per initial letter, each archived under its own name."""
    return [http_get(LETTER_URL.format(letter=ch), archive_dir, f"member-directory-{ch}.html")
            for ch in LETTERS]


def parse(paths: list[Path], source: dict) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    skipped: dict[str, int] = {}
    position = 0
    total_cards = 0
    for path in paths:
        letter = path.stem.rsplit("-", 1)[-1]
        blob = path.read_text(encoding="utf-8", errors="replace")
        cards = CARD.findall(blob)
        total_cards += len(cards)
        for card in cards:
            name = _text(NAME.pattern, card)
            if not name:
                continue
            kind = _text(MEMBERSHIP.pattern, card) or "(no membership type)"
            if not MANUFACTURER.search(kind):
                skipped[kind] = skipped.get(kind, 0) + 1
                continue
            slug_m = SLUG.search(card)
            slug = slug_m.group(1) if slug_m else ""
            if slug and slug in seen:
                continue                      # a member can appear under a trade name and a legal name
            if slug:
                seen.add(slug)
            position += 1
            out.append(contract_row(
                source, position, name=name,
                address=_text(PROP.format(p="streetAddress"), card),
                city=_text(PROP.format(p="addressLocality"), card),
                state=_text(PROP.format(p="addressRegion"), card),
                zip_code=_text(PROP.format(p="postalCode"), card),
                source_url=LETTER_URL.format(letter=letter),
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
