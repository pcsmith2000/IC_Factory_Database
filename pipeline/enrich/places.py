"""Stage 13 — a coordinate, a website and a phone from Overture's places, matched on the ADDRESS.

892 facilities carry a street address and no coordinate. Geocodio answered for most of them and
the answer was graded street_center or range_interpolation rather than rooftop, so gate E2
correctly refused to publish it. Those facilities are not unfindable; they are unfindable BY
GEOCODING.

Overture's places theme is a different kind of evidence: 60M points of interest, each with its own
address, name, and often a website and phone. Where one of them carries the same street address as
a facility, its point is a second, independent statement of where that facility is.

WHY THE ADDRESS AND NOT THE NAME. Both were measured against 241 Oregon and Washington facilities
whose coordinate came from a verified ROOFTOP geocode, so the error is knowable:

    rule                              matched   median error   p90     >1km
    name >= 0.90 fuzzy                  49%         168m       --      --
    name >= 0.90 AND street agrees      27%          60m       302m     0%
    street agrees, name ignored         55%          63m       302m    3.8%

The name requirement halved the yield and bought nothing: it was only ever a proxy for the street
check that sits behind it. Matching on the address alone found 70 of 145 address-but-no-coordinate
facilities against the name rule's 38.

WHAT THE NAME IS STILL FOR. The 3.8% tail is entirely one situation — several places sharing a
street address and sitting far apart, which is a business park or a mis-addressed POI rather than a
multi-tenant building. Split by that:

    candidates at the address      n    median   <=200m    >1km
    exactly one                   88      54m     84.1%    3.4%
    several, within 150m           32      82m     84.4%    0.0%
    several, more than 150m        13     143m     69.2%   15.4%

So a spread-out candidate set is refused unless the facility's own name picks one of them out.
That keeps about 91% of the yield and removes the failure mode that puts a pin on another
company's parcel.

WHAT THIS IS NOT. 63m is not a rooftop fix. These coordinates carry `basis: place_match`, which
survivorship ranks BELOW basis:rooftop, so a real geocode always wins and this only ever fills a
hole. Gate E7 enforces that every one of them cites the Overture place id and the address that
agreed, so any of them can be argued with.
"""
from __future__ import annotations
import math, re, collections

R = 6371008.8
RELEASE = "2026-08-19.0"
PLACES = ("s3://overturemaps-us-west-2/release/{release}"
          "/theme=places/type=place/*.parquet")
AMBIGUOUS_SPREAD_M = 150.0     # beyond this, candidates at one address are not one site
NAME_TIEBREAK = 0.90           # the score that may pick one out of a spread-out set
# A COORDINATE is a property of the SITE: any tenant of the building locates it, so the address
# match alone is enough. A WEBSITE or a PHONE is a property of the COMPANY, and the tenant has to
# be the right one. Measured on the 18 control facilities where a roster and an Overture place
# both gave a website: ungated, one of them was another company's site entirely — Mobile Modular's
# at The Truss Company's address in Eugene. A name gate at 0.90 cut exactly that row and kept the
# three where the two domains were the same company under different names (bldr.com and
# bldrwashington.com, thetrussco.com and medfordtruss.com). It halves the contact yield, and a
# confidently wrong website is worse than a missing one because a reader acts on it.
CONTACT_NAME_MIN = 0.90
SOURCE_ID = "overture:place"

_NOISE = re.compile(r"[^A-Z0-9 ]")
_SUFFIX = re.compile(r"\b(ST|STREET|RD|ROAD|AVE|AVENUE|BLVD|BOULEVARD|DR|DRIVE|LN|LANE|WAY|CT|"
                     r"COURT|PL|PLACE|HWY|HIGHWAY|PKWY|PARKWAY|N|S|E|W|NE|NW|SE|SW|STE|SUITE|"
                     r"UNIT|BLDG|BUILDING|PO|BOX|ATTN)\b")
_LEGAL = re.compile(r"\b(INC|LLC|CO|CORP|CORPORATION|COMPANY|LTD|LP|LLP|PLC|USA|THE|INCORPORATED|"
                    r"MFG|MANUFACTURING|INDUSTRIES|INDUSTRIAL|GROUP|HOLDINGS)\b")


def street_key(s: str) -> tuple[str, set[str]]:
    """(house number, distinctive street words). Enough to agree or disagree, not more.

    Suffixes and unit markers are dropped because the two sides spell them differently for the
    same building — "2802 142nd Ave. East PO Box 878" and "2802 142nd Ave E" are one address.
    """
    toks = _NOISE.sub(" ", (s or "").upper()).split()
    num = next((t for t in toks if t.isdigit()), "")
    words = {t for t in _SUFFIX.sub(" ", " ".join(toks)).split() if len(t) >= 3 and not t.isdigit()}
    return num, words


def name_score(a: str, b: str) -> float:
    """Only ever a tiebreak here, so it is deliberately blunt."""
    import difflib
    norm = lambda s: " ".join(_LEGAL.sub(" ", _NOISE.sub(" ", (s or "").upper())).split())
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    if {t for t in set(na.split()) & set(nb.split()) if len(t) >= 5}:
        r = max(r, 0.80)
    if na in nb or nb in na:
        r = max(r, 0.90)
    return r


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def spread_m(cands: list[dict]) -> float:
    if len(cands) < 2:
        return 0.0
    return max(haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
               for i, a in enumerate(cands) for b in cands[i + 1:])


def index_places(rows: list[dict]) -> dict:
    """(region, locality, house number) -> the places there. The join key both sides can produce."""
    idx: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        num, words = street_key(r.get("addr"))
        if not num or r.get("lat") is None:
            continue
        idx[((r.get("reg") or "").upper(), (r.get("loc") or "").upper(), num)].append({**r, "words": words})
    return idx


def choose(fac: dict, idx: dict) -> tuple[dict | None, str, list[dict]]:
    """The one place this facility's address names, or a reason there isn't one."""
    num, words = street_key(fac.get("address"))
    if not num:
        return None, "no house number in the address", []
    bucket = idx.get(((fac.get("state") or "").upper(), (fac.get("city") or "").upper(), num), [])
    cands = [c for c in bucket if words & c["words"]]
    if not cands:
        return None, "no Overture place at this address", []
    if len(cands) == 1:
        return cands[0], "", cands
    if spread_m(cands) <= AMBIGUOUS_SPREAD_M:
        # Several tenants of one building. Any of them locates the building, so take the first by
        # a stable key rather than by list order, which depends on how the parquet was read.
        return min(cands, key=lambda c: (c.get("nm") or "", c["lat"], c["lon"])), "", cands
    best = max(cands, key=lambda c: name_score(fac.get("name", ""), c.get("nm") or ""))
    if name_score(fac.get("name", ""), best.get("nm") or "") >= NAME_TIEBREAK:
        return best, "", cands
    return None, (f"{len(cands)} places at this address spread over "
                  f"{spread_m(cands):.0f}m and the name matches none of them"), cands


def assertions_for(fac: dict, place: dict, cands: list[dict], *, want_coord: bool = True) -> list[dict]:
    """A coordinate, and the contact details that came with it — as separate assertions.

    Separate because survivorship judges each field on its own: a registry's phone should still
    outrank this one, while the coordinate may be the only one there is.

    `want_coord` is false for a facility that already has a coordinate. It is still worth matching
    — the website and the phone are the point for most of them — but stage 13 must never restate
    a location that a rooftop geocode already settled.
    """
    from ._db import assertion
    ev = (f"overture:{RELEASE}:place:{place.get('id') or place.get('nm') or '?'} :: "
          f"{place.get('addr')} :: matched {fac.get('address')} "
          f"[{len(cands)} candidate(s), spread {spread_m(cands):.0f}m]")
    out = []
    if want_coord:
        out.append(assertion(fac["facility_id"], "lat_lon", f"{place['lat']:.6f},{place['lon']:.6f}",
                             source_id=SOURCE_ID, basis="place_match", confidence=0.75, evidence=ev))
    # The name gate, and only here. See CONTACT_NAME_MIN.
    score = name_score(fac.get("name", ""), place.get("nm") or "")
    if score < CONTACT_NAME_MIN:
        return out
    cev = f"{ev} :: place named {place.get('nm')!r}, name score {score:.2f}"
    if (place.get("website") or "").strip():
        out.append(assertion(fac["facility_id"], "website", place["website"].strip(),
                             source_id=SOURCE_ID, basis="place_match", confidence=0.75, evidence=cev))
    if (place.get("phone") or "").strip():
        from ..contract import phone_digits
        if (p := phone_digits(place["phone"])):
            out.append(assertion(fac["facility_id"], "phone", p, source_id=SOURCE_ID,
                                 basis="place_match", confidence=0.75, evidence=cev))
    return out


def run(facilities: list[dict], place_rows: list[dict],
        need_coord: set[str] | None = None) -> dict:
    """Facilities with an address x the places slice covering them -> assertions + counts.

    Eligibility is every facility that HAS an address, not only those missing a coordinate. The
    coordinate is the narrower prize: 892 facilities want one. The website and the phone are the
    broader one — 5,261 facilities have an address and only 984 have a website, because the
    regulators that supply most addresses publish no contact details at all and the association
    rosters that do are mostly attached to rows the classifier discards. `need_coord` says which
    facilities may receive a lat_lon; everything else is matched for its contact details only.
    """
    idx = index_places(place_rows)
    assertions, matched, reasons = [], [], collections.Counter()
    bucket_counts = collections.Counter()
    for fac in facilities:
        place, why, cands = choose(fac, idx)
        if place is None:
            reasons[why] += 1
            continue
        bucket_counts["one candidate" if len(cands) == 1 else
                      ("several, one site" if spread_m(cands) <= AMBIGUOUS_SPREAD_M
                       else "several, name broke the tie")] += 1
        want = need_coord is None or fac["facility_id"] in need_coord
        got = assertions_for(fac, place, cands, want_coord=want)
        if got:
            assertions += got
            matched.append(fac["facility_id"])
        else:
            reasons["matched, but it has a coordinate and the name does not match"] += 1
    return {"eligible": len(facilities), "places_indexed": len(idx),
            "matched": len(matched), "assertions": assertions,
            "by_field": dict(collections.Counter(a["field"] for a in assertions)),
            "candidate_shape": dict(bucket_counts), "refused": dict(reasons),
            "overture_release": RELEASE}


# ---------------------------------------------------------------- reading the slice
def state_boxes(rows: list[dict], pad_deg: float = 1.0) -> dict[str, tuple]:
    """A bounding box per state, derived from the coordinates the release already holds.

    Overture is read by bbox because that is what pushes down to the parquet row groups; a scan
    filtered on addresses[1].region reads every file instead. The extents could come from a table
    of state boundaries, but the database already knows roughly where each state is — 4,617
    facilities carry a coordinate — and deriving it keeps a reference table nobody maintains out
    of the repo. Padded by a degree (~111km) because those points cluster where the industry is,
    not at the state line; the box is recorded per run so a miss can be diagnosed rather than
    guessed at.
    """
    pts: dict[str, list[tuple[float, float]]] = collections.defaultdict(list)
    for r in rows:
        ll = (r.get("lat_lon") or "").strip()
        st = (r.get("state") or "").strip().upper()
        if not ll or not st:
            continue
        try:
            lat, lon = (float(x) for x in ll.split(",")[:2])
        except ValueError:
            continue
        pts[st].append((lat, lon))
    return {st: (min(p[1] for p in v) - pad_deg, min(p[0] for p in v) - pad_deg,
                 max(p[1] for p in v) + pad_deg, max(p[0] for p in v) + pad_deg)
            for st, v in pts.items()}


def fetch(states: list[str], boxes: dict[str, tuple], release: str = RELEASE) -> list[dict]:
    """Every named, addressed Overture place inside the boxes of the given states."""
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("CREATE OR REPLACE SECRET ov (TYPE s3, PROVIDER config, REGION 'us-west-2');")
    where = " OR ".join(
        f"(bbox.xmin BETWEEN {b[0]} AND {b[2]} AND bbox.ymin BETWEEN {b[1]} AND {b[3]})"
        for s in states if (b := boxes.get(s)))
    if not where:
        return []
    rows = con.execute(f"""
        SELECT id, names.primary nm, addresses[1].freeform addr,
               upper(addresses[1].locality) loc, upper(addresses[1].region) reg,
               websites[1] website, phones[1] phone, ST_Y(geometry) lat, ST_X(geometry) lon
        FROM read_parquet('{PLACES.format(release=release)}')
        WHERE ({where}) AND addresses[1].freeform IS NOT NULL
    """).fetchall()
    cols = ("id", "nm", "addr", "loc", "reg", "website", "phone", "lat", "lon")
    return [dict(zip(cols, r)) for r in rows]
