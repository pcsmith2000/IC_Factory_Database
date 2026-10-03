"""Job-site rows: an inspection or permit record at a construction site, published as a plant.

OSHA inspects a truss company where its crew is working, and that is often a house in a
subdivision the company delivered to. EPA FRS turns each OSHA-only inspection into a facility of
its own, named "<activity number> - <establishment>" (WA317940369 - TRUSS COMPANY & BUILDING SUPPLY
INC THE, at HARBOR HILL S5LOT 1, Gig Harbor), and the 2026-09-18 OSHA collection carries the same
shape (WA317976402 - THE TRUSS COMPANY & BUILDING SUPPLY LLC, 4497 Wandering Way, Port Orchard).
The address is where the inspector stood, which is the strongest evidence the pipeline has that a
building exists there, and no evidence at all that a factory does.

A rule on the prefix alone would be wrong: WA317946391 - PACIFIC WOODTECH CORPORATION at 1850 Park
Lane, Burlington is the company's real plant, inspected at its own door. So this module only
proposes, on evidence, and a person rules (control/operator_assertions.csv, existence_flag not_ic).
It never writes the warehouse and never sets existence_flag.

A row is a candidate when it is an INSPECTION RECORD and corroborating signals hold: one when
the name carries the inspection prefix, two when only its sources say so (EPA FRS files many real
plants from OSHA alone; Louws Truss's Ferndale plant is one, and one signal would list it):

  inspection record        the name carries an OSHA activity / inspection-number prefix
                           ("WA317946391 - ", "317711188 - ", "70260 - ", "CPX2024XEG419X0024 - "),
                           or every source of its address is a site visit (OSHA, or EPA FRS's
                           OSHA-OIS programme rows), with nothing that lists the plant itself
  company_plant_elsewhere  the same company has a plant record in the same state that is NOT an
                           inspection record, at a different site: the inspection was away from
                           its establishment ("The Truss Company" is in golden at its 8 plants)
  no_plant_scale_building  the building pass measured the buildings near the point and none is of
                           PLANT_SQFT or more: a house, not a truss plant
  no_building_found        the building pass ran and found no building at all (an empty lot, or a
                           gap in Overture's coverage, so it never makes a row `likely`)
  lot_or_subdivision       the address names a lot, tract, plat, block, phase or subdivision

Counter-evidence keeps a row off the list: the address is one of the company's own plant records;
a building of PLANT_SQFT or more contains the point; or a plant-scale building stands nearby and the
company has no other plant on record.

tier: `likely` when nothing measured near the point is bigger than a large house (HOUSE_SQFT) AND
another signal agrees; `review` otherwise. Either way it is a proposal for a person.

    python -m pipeline.jobsite [--out control/jobsite-worklist.csv]   # read-only
"""
from __future__ import annotations
import csv, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLANT_SQFT = 10_000           # the smallest building a truss or panel line runs in; a house is far below it
HOUSE_SQFT = 5_000            # `likely` asks for more: nothing near the point bigger than a large house

# An OSHA activity / inspection number in front of the establishment name, " - " separated. State-plan
# numbers come with a state prefix (WA317…), federal ones without (317705860, 105547), Arizona's
# ADOSH as CPX<year>X…. Five digits is the floor: a four-digit run is a street or a model number.
INSPECTION_PREFIX = re.compile(r"^\s*(?:[A-Z]{2}\d{6,}|\d{5,}|CPX\d{4}X[0-9A-Z]+)\s+-\s+", re.I)
LOT = re.compile(r"\bLOTS?\b|LOT\s*#?\s*\d|\bSUBDIV(?:ISION)?\b|\bPLAT\b|\bTRACT\b|\bPHASE\s+\w+|\bBLK\b|\bBLOCK\s+\d",
                 re.I)
LEGAL = {"inc", "incorporated", "llc", "l", "c", "corp", "corporation", "ltd", "co", "the", "dba", "and", "of"}


def strip_prefix(name: str) -> str:
    return INSPECTION_PREFIX.sub("", name or "").strip()


def company_tokens(name: str) -> tuple[str, ...]:
    """The company a name points at, in order: inspection prefix, branch suffix (" - Pasco") and
    legal words dropped. "The Truss Company - Pasco" is (truss, company); "TRUSS COMPANY & BUILDING
    SUPPLY INC THE" is (truss, company, building, supply)."""
    s = strip_prefix(name)
    s = re.split(r"\s+-\s+", s, maxsplit=1)[0]
    words = re.sub(r"[^a-z0-9 ]", " ", s.lower()).split()
    return tuple(w for w in words if w not in LEGAL)


# Words every second name in this trade carries. A company named only with them ("The Truss
# Company", "Truss Systems") is matched by how its name STARTS, never by a word found anywhere:
# "Ariel Truss Company" is not The Truss Company.
GENERIC = {"truss", "trusses", "company", "building", "buildings", "supply", "systems", "system",
           "components", "component", "structures", "structural", "steel", "metal", "metals", "homes",
           "home", "precast", "concrete", "manufacturing", "mfg", "industries", "products", "lumber",
           "panel", "panels", "modular", "builders", "construction", "group", "enterprises",
           "fabricators", "fabrication", "wood", "timber", "international", "american", "united",
           "national", "roof", "design", "solutions", "services", "materials", "custom"}


def same_company(a, b) -> bool:
    """The shorter name's words all in the longer one, and the shorter one is either distinctive (a
    word outside GENERIC, and two words or eight letters) or the longer name starts with it."""
    if not a or not b:
        return False
    small, big = (tuple(a), tuple(b)) if len(a) <= len(b) else (tuple(b), tuple(a))
    if not set(small) <= set(big):
        return False
    if big[:len(small)] == small and (len(small) >= 2 or len(small[0]) >= 8):
        return True
    distinctive = [w for w in small if w not in GENERIC]
    return bool(distinctive) and (len(small) >= 2 or len("".join(small)) >= 8)


def _house(addr: str) -> str:
    m = re.match(r"\s*(\d+)", addr or "")
    return m.group(1) if m else ""


def different_site(row: dict, other: dict) -> bool | None:
    """True: another site. False: the same one. None: cannot tell (an address missing in one city)."""
    city = lambda r: re.sub(r"[^a-z]", "", (r.get("city") or "").lower())
    if city(row) and city(other) and city(row) != city(other):
        return True
    a, b = row.get("address") or "", other.get("address") or ""
    if not a or not b:
        return None
    if _house(a) != _house(b):
        return True
    from .recovery.streets import street_equivalent
    return not street_equivalent(a, b)[0]


def is_inspection_record(row: dict) -> bool:
    return bool(INSPECTION_PREFIX.match(row.get("name") or "")) or bool(row.get("inspection_only"))


def _int(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def candidates(rows: list[dict]) -> list[dict]:
    """rows: golden facilities with facility_id, name, address, city, state, and when known
    inspection_only (bool), max_building_sqft, point_building_sqft, building_review (outcome).
    Pure: the same rows always give the same list."""
    plants: dict[str, list[tuple[frozenset, dict]]] = {}
    for r in rows:
        if not is_inspection_record(r):
            plants.setdefault((r.get("state") or "").upper(), []).append((company_tokens(r.get("name")), r))
    out = []
    for r in rows:
        if not is_inspection_record(r):
            continue
        tokens = company_tokens(r.get("name"))
        own = [p for toks, p in plants.get((r.get("state") or "").upper(), [])
               if p["facility_id"] != r["facility_id"] and same_company(tokens, toks)]
        if any(different_site(r, p) is False for p in own):
            continue                  # inspected at one of the company's own plants: that is a plant
        elsewhere = [p for p in own if different_site(r, p) is True]
        max_sqft, point_sqft = _int(r.get("max_building_sqft")), _int(r.get("point_building_sqft"))
        reviewed = bool(r.get("building_review"))
        signals = []
        if elsewhere:
            signals.append("company_plant_elsewhere")
        if reviewed and max_sqft is not None and max_sqft < PLANT_SQFT:
            signals.append("no_plant_scale_building")
        elif reviewed and max_sqft is None:
            signals.append("no_building_found")      # weaker: Overture misses rural buildings too
        if LOT.search(r.get("address") or ""):
            signals.append("lot_or_subdivision")
        if len(signals) < (1 if INSPECTION_PREFIX.match(r.get("name") or "") else 2):
            continue
        if (point_sqft or 0) >= PLANT_SQFT:
            continue                  # the point sits on a plant-scale building: it is a plant
        if (max_sqft or 0) >= PLANT_SQFT and not elsewhere:
            continue                  # a plant-scale building beside it, and no other plant: Pacific Woodtech
        tier = "likely" if (max_sqft is not None and max_sqft < HOUSE_SQFT and len(signals) >= 2) else "review"
        out.append({
            "facility_id": r["facility_id"], "tier": tier, "name": r.get("name") or "",
            "address": r.get("address") or "", "city": r.get("city") or "", "state": r.get("state") or "",
            "signals": " ".join(signals),
            "company_plants": " ".join(sorted(f"{p['facility_id']}:{p.get('city') or ''}" for p in elsewhere)),
            "max_building_sqft": "" if max_sqft is None else max_sqft,
            "building_review": r.get("building_review") or "",
        })
    return sorted(out, key=lambda c: (c["tier"] != "likely", c["state"], strip_prefix(c["name"]).lower(), c["facility_id"]))


# Every golden facility, its address sources and the buildings near it. Read-only. An address is
# "inspection only" when each assertion of it is a site visit or a class that never lists a plant
# on its own (enrichment, people, web research corroborating it).
LOAD_SQL = """
    WITH addr AS (
        SELECT permanent_facility_id AS fid,
               MIN(CASE WHEN COALESCE(site_visit, 0) = 1 OR source_class IN
                   ('enrichment', 'human_feedback', 'monitor_fix', 'operator', 'web_research') THEN 1 ELSE 0 END)
                   AS inspection_only
        FROM v_assertions_resolved
        WHERE field_key = 'address' AND permanent_facility_id IS NOT NULL
        GROUP BY permanent_facility_id),
    bld AS (
        SELECT fb.facility_key AS fid, MAX(b.area_sqft) AS max_sqft,
               MAX(CASE WHEN fb.contains_point = 1 THEN b.area_sqft END) AS point_sqft
        FROM facility_building fb JOIN building_footprint b ON b.building_id = fb.building_id
        WHERE fb.status <> 'rejected'
        GROUP BY fb.facility_key)
    SELECT g.facility_key AS facility_id, g.name, g.address, g.city, g.state,
           COALESCE(a.inspection_only, 0) AS inspection_only,
           bld.max_sqft AS max_building_sqft, bld.point_sqft AS point_building_sqft,
           r.outcome AS building_review
    FROM golden_facility g
    LEFT JOIN addr a ON a.fid = g.facility_key
    LEFT JOIN bld ON bld.fid = g.facility_key
    LEFT JOIN facility_building_review r ON r.facility_key = g.facility_key
"""

COLUMNS = ["facility_id", "tier", "name", "address", "city", "state", "signals", "company_plants",
           "max_building_sqft", "building_review"]


def load(wh) -> list[dict]:
    return wh.query(LOAD_SQL)


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    from .warehouse import open_warehouse, SqliteWarehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.jobsite")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    ap.add_argument("--out", default=str(ROOT / "control" / "jobsite-worklist.csv"))
    args = ap.parse_args(argv)
    wh = (SqliteWarehouse(Path(args.db)) if args.db
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    found = candidates(load(wh))
    write_csv(found, Path(args.out))
    print(f"{len(found)} job-site candidates ({sum(c['tier'] == 'likely' for c in found)} likely) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
