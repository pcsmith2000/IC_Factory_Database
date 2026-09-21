"""Recover rooftop coordinates from an exact Overture place/building intersection.

An Overture place point alone is not rooftop evidence.  This pass requires the facility name and
full address to agree with the place record, then requires that point to be inside exactly one
Overture building polygon.  A dry-run control sample compares the rule with existing verified
rooftop coordinates before any assertion may be written.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

from pipeline.enrich import footprint, places
from pipeline.enrich._db import assertion, _rows_for

SOURCE_ID = "overture:place-building"
NAME_MIN = 0.90
CONTROL_MIN = 10
CONTROL_P90_MAX_M = 150.0
CONTROL_MAX_M = 500.0


def _zip5(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[:5] if len(digits) >= 5 else ""


def strict_address_match(fac: dict, place: dict) -> tuple[bool, str]:
    """Require the same named business at the same complete physical address."""
    fnum, fwords = places.street_key(fac.get("address") or "")
    pnum, pwords = places.street_key(place.get("addr") or "")
    if not fnum or fnum != pnum:
        return False, "street_number_mismatch"
    if not fwords or fwords != pwords:
        return False, "street_name_mismatch"
    if (fac.get("state") or "").strip().upper() != (place.get("reg") or "").strip().upper():
        return False, "state_mismatch"
    if (fac.get("city") or "").strip().upper() != (place.get("loc") or "").strip().upper():
        return False, "city_mismatch"
    fzip, pzip = _zip5(fac.get("zip")), _zip5(place.get("zip"))
    if fzip and pzip and fzip != pzip:
        return False, "zip_mismatch"
    if places.name_score(fac.get("name") or "", place.get("nm") or "") < NAME_MIN:
        return False, "name_mismatch"
    return True, "matched"


def strict_choose(fac: dict, place_rows: list[dict]) -> tuple[dict | None, str]:
    matches = []
    reasons = Counter()
    for p in place_rows:
        ok, why = strict_address_match(fac, p)
        if ok:
            matches.append(p)
        else:
            reasons[why] += 1
    if not matches:
        return None, reasons.most_common(1)[0][0] if reasons else "no_place_candidate"
    # Several POI records may describe the same tenant.  They are safe only when they resolve to
    # the same point (within normal coordinate rounding); otherwise this pass cannot choose a roof.
    points = {(round(float(p["lat"]), 5), round(float(p["lon"]), 5)) for p in matches}
    if len(points) != 1:
        return None, "ambiguous_place_points"
    return max(matches, key=lambda p: (places.name_score(fac.get("name") or "", p.get("nm") or ""),
                                       str(p.get("id") or ""))), "matched"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def control_gate(distances_m: list[float]) -> tuple[bool, dict]:
    p90 = percentile(distances_m, .90)
    maximum = max(distances_m) if distances_m else None
    passed = (len(distances_m) >= CONTROL_MIN and p90 is not None and
              p90 <= CONTROL_P90_MAX_M and maximum is not None and maximum <= CONTROL_MAX_M)
    return passed, {"matched_controls": len(distances_m),
                    "median_error_m": None if not distances_m else round(percentile(distances_m, .50), 1),
                    "p90_error_m": None if p90 is None else round(p90, 1),
                    "max_error_m": None if maximum is None else round(maximum, 1),
                    "minimum_controls": CONTROL_MIN,
                    "p90_limit_m": CONTROL_P90_MAX_M,
                    "max_limit_m": CONTROL_MAX_M}


def _candidate_places(facilities: list[dict], place_rows: list[dict]) -> tuple[list[dict], Counter]:
    # Pre-bucket by state, city and house number so strict matching remains linear in the slice.
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for p in place_rows:
        num, _ = places.street_key(p.get("addr") or "")
        buckets.setdefault(((p.get("reg") or "").upper(), (p.get("loc") or "").upper(), num), []).append(p)
    selected, refused = [], Counter()
    for fac in facilities:
        num, _ = places.street_key(fac.get("address") or "")
        bucket = buckets.get(((fac.get("state") or "").upper(),
                              (fac.get("city") or "").upper(), num), [])
        place, why = strict_choose(fac, bucket)
        if place is None:
            refused[why] += 1
        else:
            selected.append({"facility": fac, "place": place,
                             "facility_id": fac["facility_id"],
                             "lat": float(place["lat"]), "lon": float(place["lon"])})
    return selected, refused


def _append(db, row: dict, candidate: dict, building: dict, workflow: str) -> int:
    fac, place = candidate["facility"], candidate["place"]
    evidence = json.dumps({
        "workflow": workflow, "overture_release": places.RELEASE,
        "place_id": place.get("id"), "building_id": building["building_id"],
        "place_address": place.get("addr"), "place_city": place.get("loc"),
        "place_state": place.get("reg"), "place_zip": place.get("zip"),
        "facility_address": fac.get("address"), "facility_city": fac.get("city"),
        "facility_state": fac.get("state"), "facility_zip": fac.get("zip"),
        "name_score": round(places.name_score(fac.get("name") or "", place.get("nm") or ""), 3),
        "checks": ["exact normalized street number and words", "city and state",
                   "postal code when both sources provide one", "facility name >= 0.90",
                   "place point intersects exactly one Overture building polygon"],
    }, sort_keys=True)
    a = assertion(row["facility_id"], "lat_lon", f"{candidate['lat']:.7f},{candidate['lon']:.7f}",
                  source_id=SOURCE_ID, basis="rooftop", confidence=0.90,
                  evidence=workflow + " :: " + evidence)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ev, fact = _rows_for(a, row["live_release"], now[:10], now)
    db.execute("INSERT INTO dim_source(source_key,source_id,name,class,status) VALUES(%s,%s,%s,'enrichment','active') ON CONFLICT(source_key) DO NOTHING",
               (SOURCE_ID, SOURCE_ID, "Overture exact place inside building footprint"))
    db.execute("INSERT INTO ref_source_row(row_hash,source_key,source_url,source_document,retrieved_date,facility_key,match_method,match_confidence,last_seen_release) VALUES(" + ",".join(["%s"] * 9) + ") ON CONFLICT(row_hash) DO NOTHING", ev)
    inserted = len(db.execute("INSERT INTO fact_assertions(assertion_id,release_tag,facility_key,source_key,field_key,date_key,value,basis,site_visit,row_hash,confidence,source_class,asserted_at) VALUES(" + ",".join(["%s"] * 13) + ") ON CONFLICT(assertion_id,release_tag) DO NOTHING RETURNING assertion_id", fact).fetchall())
    saved = db.execute("SELECT a.value,a.basis,r.source_url FROM fact_assertions a JOIN ref_source_row r ON r.row_hash=a.row_hash WHERE a.assertion_id=%s AND a.release_tag=%s", (fact[0], row["live_release"])).fetchone()
    if not saved or saved["value"] != a["value"] or saved["basis"] != "rooftop" or saved["source_url"] != workflow:
        raise RuntimeError("Overture coordinate provenance verification failed")
    db.execute("UPDATE coordinate_recovery_rows SET status='rooftop_asserted' WHERE campaign_id=%s AND facility_id=%s", (row["campaign_id"], row["facility_id"]))
    return inserted


def run(db, campaign_id: str, targets: list[dict], row_limit: int, workflow: str,
        write: bool = False, max_files: int = 30) -> dict:
    targets = targets[:row_limit]
    if not targets:
        return {"targets": 0, "coordinate_assertions_inserted": 0, "new_api_calls": 0}
    states = sorted({(r["baseline"].get("state") or "").upper() for r in targets})
    controls = db.execute("""SELECT g.facility_key AS facility_id,g.name,g.address,g.city,g.state,g.zip,g.lat_lon
        FROM golden_facility g WHERE g.state=ANY(%s) AND g.address ~ '^[0-9]+[A-Za-z]? '
        AND NULLIF(btrim(g.lat_lon),'') IS NOT NULL
        AND EXISTS (SELECT 1 FROM fact_assertions a WHERE a.facility_key=g.facility_key
                    AND a.field_key='lat_lon' AND a.basis='rooftop')
        ORDER BY md5(g.facility_key) LIMIT 100""", (states,)).fetchall()
    facilities = [{"facility_id": r["facility_id"], **r["baseline"]} for r in targets] + [dict(r) for r in controls]
    all_golden = db.execute("SELECT state,lat_lon FROM golden_facility WHERE NULLIF(btrim(lat_lon),'') IS NOT NULL").fetchall()
    boxes = places.state_boxes([dict(r) for r in all_golden])
    localities = {(r.get("city") or "").upper() for r in facilities}
    postcodes = {_zip5(r.get("zip")) for r in facilities if _zip5(r.get("zip"))}
    place_rows = places.fetch(states, boxes, localities=localities, postcodes=postcodes)
    candidates, refused = _candidate_places(facilities, place_rows)
    contained = footprint.containing(
        [{"facility_id": c["facility_id"], "lat": c["lat"], "lon": c["lon"]} for c in candidates],
        release=places.RELEASE, cache=Path("recovery-summary/overture-building-index.json"),
        max_files=max_files)
    by_id = {r["facility_id"]: r for r in contained}
    cand_by_id = {c["facility_id"]: c for c in candidates}
    control_ids = {r["facility_id"] for r in controls}
    distances, accepted_targets, building_refused = [], [], Counter()
    control_coords = {}
    for r in controls:
        try:
            control_coords[r["facility_id"]] = tuple(float(x) for x in r["lat_lon"].split(",")[:2])
        except (ValueError, AttributeError):
            pass
    for fid, result in by_id.items():
        buildings = result.get("buildings") or []
        if len(buildings) != 1:
            building_refused[result.get("reason") or "ambiguous_building"] += 1
            continue
        c = cand_by_id[fid]
        if fid in control_ids and fid in control_coords:
            lat, lon = control_coords[fid]
            distances.append(places.haversine_m(lat, lon, c["lat"], c["lon"]))
        elif fid not in control_ids:
            accepted_targets.append((c, buildings[0]))
    gate_passed, validation = control_gate(distances)
    inserted = 0
    if write:
        if not gate_passed:
            raise RuntimeError("Overture rooftop control gate failed; no assertions written")
        rows = {r["facility_id"]: r for r in targets}
        for candidate, building in accepted_targets:
            row = rows[candidate["facility_id"]]
            row["campaign_id"] = campaign_id
            inserted += _append(db, row, candidate, building, workflow)
    return {"targets": len(targets), "controls_sampled": len(controls),
            "overture_places_read": len(place_rows), "exact_place_candidates": len(candidates),
            "target_rooftops_accepted": len(accepted_targets),
            "place_refusals": dict(refused), "building_refusals": dict(building_refused),
            "validation": validation, "control_gate_passed": gate_passed,
            "write_requested": write, "coordinate_assertions_inserted": inserted,
            "new_api_calls": 0, "api_cost_usd": 0.0, "overture_release": places.RELEASE}
