"""Stage 14 — a coordinate Geocodio computed, refused by its own label, and confirmed by a building.

Stage 10 asks Geocodio for every address and publishes the answer only when `accuracy_type` is
`rooftop`. That rule is right about rooftop and wrong about what it rejects: accuracy_type is a
label Geocodio attaches about its own METHOD, not a measurement of where the point landed. A
`range_interpolation` point for County Prestress & Precast in Salem, Illinois sits about twenty
feet from the plant, on the parcel, and was discarded — while stage 11, which reads the Overture
building under a coordinate, was never pointed at it.

So this stage replaces the label with two measurements the database can make for itself:

  1. THE POSTCODE AGREES. Geocodio returns a result even when it has silently moved to another
     town — 300 Wright Road, Poca WV came back as 300 Wrights Ln, Buffalo WV, with an accuracy
     score of 0.88. Comparing the postcode it echoed against the one we hold catches that. It is
     free, both sides are already in hand, and it removes 11% of the pool (24% of street_center).
  2. A BUILDING IS THERE. Overture's building footprints, queried around the point. Measured on
     62 postcode-verified candidates: 27% within 25m of a building, 62% within 50m, 83% within
     100m, and one with nothing inside 390m.

Stage 11's radius is 30m because a rooftop geocode is supposed to be ON the roof. An interpolated
point is placed from the house number along the street and lands at the kerb, so 30m is the wrong
ruler for it — it confirms only 32% of these. 50m is the default here.

What this is NOT: a rooftop fix, or proof that the building found is the right one. The nearest
building had a median footprint of 3,993 sqft in the sample, which is an office or a shed as often
as a plant. The claim is only what the evidence supports — there is a structure at this point, and
here is its Overture id and its distance — and `basis:interpolated_on_building` ranks below both
`rooftop` and `place_match` so it never displaces a better answer.
"""
from __future__ import annotations
import collections, math, re

from . import footprint

ADMITTED = ("range_interpolation", "street_center")
DEFAULT_RADIUS_M = 50.0
SOURCE_ID = "geocode:geocodio"          # the coordinate IS Geocodio's; this stage only vouches for it
BASIS = "interpolated_on_building"
_TRAILING_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")


def ours_zip(fac: dict) -> str:
    return re.sub(r"[^0-9]", "", fac.get("zip") or "")[:5]


def theirs_zip(formatted: str) -> str:
    """The postcode at the END of Geocodio's formatted address.

    Not the first five digits of the string: "749 W Commercial St, Salem, IL 62881" begins with a
    house number, and reading that as a postcode reported an 88% mismatch rate where the truth was
    11%. A wrong measurement that flatters nothing is still a wrong measurement.
    """
    m = _TRAILING_ZIP.search((formatted or "").strip())
    return m.group(1) if m else ""


def zip_agrees(fac: dict, formatted: str) -> bool:
    a, b = ours_zip(fac), theirs_zip(formatted)
    return bool(a) and a == b


def from_ledger(db, facilities: list[dict], one_line) -> tuple[list[dict], dict]:
    """The coordinates stage 10 computed, paid for, cached and refused. No new lookups.

    Returns (candidates, why-not counts). A facility with no cached answer has simply never been
    geocoded; one whose cached answer was `rooftop` is not this stage's business.
    """
    from . import cache as lookup_cache
    import json as _json
    out, why = [], collections.Counter()
    for f in facilities:
        line = one_line(f)
        hit = lookup_cache.get(db, lookup_cache.geocode_key(line), provider="geocodio")
        if not hit:
            why["never geocoded"] += 1
            continue
        res = hit["result"]
        if isinstance(res, str):
            res = _json.loads(res)
        hits = (res.get("response") or {}).get("results") or []
        if not hits:
            why["geocodio returned nothing"] += 1
            continue
        h = hits[0]
        at = h.get("accuracy_type")
        if at == "rooftop":
            why["already a rooftop coordinate"] += 1
            continue
        if at not in ADMITTED:
            why[f"accuracy_type {at} is not admitted"] += 1
            continue
        if not zip_agrees(f, h.get("formatted_address")):
            why["the postcode it returned is not ours"] += 1
            continue
        out.append({"facility_id": f["facility_id"], "lat": h["location"]["lat"],
                    "lon": h["location"]["lng"], "accuracy_type": at,
                    "accuracy": h.get("accuracy"), "formatted": h.get("formatted_address"),
                    "dataset": h.get("source"), "address_line": line})
    return out, dict(why)


def with_buildings(cands: list[dict], radius_m: float = DEFAULT_RADIUS_M,
                   release: str = footprint.DEFAULT_RELEASE, cache_path=None,
                   max_files: int | None = None, box_deg: float = 0.0035) -> list[dict]:
    """Attach the nearest Overture building to each candidate, or say none is near.

    Deliberately not footprint.measure(): that one caches its answer under (lat, lon, release) and
    a result found with a 30m radius must not be served to a 50m question. Same index, same file
    grouping, same area maths — different question, so its own query.
    """
    idx = footprint.build_index(release, cache_path)
    by_file: dict[str, list[dict]] = collections.defaultdict(list)
    out: list[dict] = []
    for c in cands:
        f = footprint.file_for(idx, c["lat"], c["lon"])
        if f is None:
            out.append({**c, "building_id": None, "reason": "outside every Overture file bbox"})
        else:
            by_file[f].append(c)
    files = list(by_file)
    if max_files is not None and len(files) > max_files:
        for f in files[max_files:]:
            for c in by_file[f]:
                out.append({**c, "building_id": None, "reason": "deferred: file ceiling reached"})
        files = files[:max_files]
    if not files:
        return out
    con = footprint._connect()
    m_per_deg = math.pi * footprint.R / 180
    for f in files:
        ps = by_file[f]
        where = " OR ".join(f"(bbox.xmin BETWEEN {p['lon']-box_deg} AND {p['lon']+box_deg} AND "
                            f"bbox.ymin BETWEEN {p['lat']-box_deg} AND {p['lat']+box_deg})" for p in ps)
        con.execute(f"CREATE OR REPLACE TEMP TABLE src AS SELECT geometry, bbox, id "
                    f"FROM read_parquet('{f}') WHERE {where}")
        for p in ps:
            rows = con.execute(
                "SELECT ST_AsText(geometry), ST_Distance(geometry, ST_Point(?, ?)), id FROM src "
                "WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ? ORDER BY 2 LIMIT 20",
                [p["lon"], p["lat"], p["lon"]-box_deg, p["lon"]+box_deg,
                 p["lat"]-box_deg, p["lat"]+box_deg]).fetchall()
            near = [(w, d * m_per_deg, bid) for w, d, bid in rows if d * m_per_deg <= radius_m]
            if not near:
                out.append({**p, "building_id": None,
                            "reason": f"no Overture building within {radius_m:.0f}m"})
                continue
            w, dist, bid = min(near, key=lambda x: x[1])
            out.append({**p, "building_id": bid, "offset_m": round(dist, 1),
                        "building_sqft": round(footprint.wkt_area_m2(w) * footprint.M2_FT2),
                        "n_within_radius": len(near), "overture_release": release})
    return out


def assertions_for(c: dict) -> list[dict]:
    from ._db import assertion
    ev = (f"geocodio:{c.get('dataset') or '?'}:{c['accuracy_type']} "
          f"(acc {c.get('accuracy')}) :: {c.get('formatted')} :: confirmed by "
          f"overture:{c.get('overture_release')}:building:{c['building_id']} "
          f"at {c['offset_m']}m, {c['building_sqft']:,} sqft")
    return [assertion(c["facility_id"], "lat_lon", f"{c['lat']:.6f},{c['lon']:.6f}",
                      source_id=SOURCE_ID, basis=BASIS, confidence=0.6, evidence=ev)]


def run(facilities: list[dict], db, one_line, radius_m: float = DEFAULT_RADIUS_M,
        release: str = footprint.DEFAULT_RELEASE, cache_path=None,
        max_files: int | None = None) -> dict:
    cands, why = from_ledger(db, facilities, one_line)
    placed = with_buildings(cands, radius_m, release, cache_path, max_files)
    asserts, confirmed = [], []
    for c in placed:
        if not c.get("building_id"):
            why[c.get("reason", "no building")] = why.get(c.get("reason", "no building"), 0) + 1
            continue
        asserts += assertions_for(c)
        confirmed.append(c)
    offs = sorted(c["offset_m"] for c in confirmed)
    return {"eligible": len(facilities), "coordinates_in_the_ledger": len(cands),
            "confirmed_by_a_building": len(confirmed), "assertions": asserts,
            "radius_m": radius_m, "refused": why,
            "accuracy_mix": dict(collections.Counter(c["accuracy_type"] for c in confirmed)),
            "offset_median_m": offs[len(offs) // 2] if offs else None,
            "offset_max_m": max(offs) if offs else None,
            "overture_release": release}
