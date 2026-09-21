"""Reuse an existing verified rooftop for an exact duplicate site record.

This pass never searches the web and never calls a provider.  It only links an unresolved frozen
row to a rooftop assertion already in the warehouse.  The linkage is deliberately narrower than
ordinary entity resolution: normalized name, city and state must all agree; the name must be
distinctive; and every qualifying donor must resolve to one coordinate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import re

from pipeline.enrich._db import assertion, _rows_for
from pipeline.reconcile import norm_name

SOURCE = "internal:exact-site-rooftop-crossmatch"


def norm_place(value: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(value or "").lower()).split())


def distinctive(name: str | None) -> bool:
    n = norm_name(name or "")
    return bool(n and (" " in n or len(n) >= 8))


def coordinate(value: str | None) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x.strip()) for x in str(value or "").split(",")[:2])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def key(row: dict) -> tuple[str, str, str] | None:
    if not distinctive(row.get("name")):
        return None
    out = (norm_name(row.get("name") or ""), norm_place(row.get("city")),
           norm_place(row.get("state")))
    return out if all(out) else None


def choose(targets: list[dict], donors: list[dict]) -> tuple[list[dict], Counter]:
    """Return unambiguous target/donor pairs and aggregate refusal reasons."""
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for donor in donors:
        if (k := key(donor)) and coordinate(donor.get("value")):
            grouped[k].append(donor)
    selected, refused = [], Counter()
    for target in targets:
        k = key(target)
        if not k:
            refused["target_missing_distinctive_name_city_state"] += 1
            continue
        matches = [d for d in grouped.get(k, []) if d["facility_id"] != target["facility_id"]]
        if not matches:
            refused["no_exact_site_rooftop_donor"] += 1
            continue
        points = {tuple(round(x, 6) for x in coordinate(d["value"])) for d in matches}
        if len(points) != 1:
            refused["conflicting_donor_rooftops"] += 1
            continue
        donor = min(matches, key=lambda d: (d["facility_id"], d.get("assertion_id") or ""))
        selected.append({"target": target, "donor": donor, "point": next(iter(points)),
                         "donor_count": len(matches)})
    return selected, refused


def _append(db, campaign: str, item: dict, workflow: str) -> int:
    target, donor = item["target"], item["donor"]
    lat, lon = item["point"]
    document = json.dumps({
        "workflow": workflow,
        "match_method": "exact normalized distinctive name + exact city + exact state",
        "donor_facility_id": donor["facility_id"],
        "donor_assertion_id": donor.get("assertion_id"),
        "donor_source_key": donor.get("source_key"),
        "donor_row_hash": donor.get("row_hash"),
        "donor_source_url": donor.get("source_url"),
        "donor_count_at_identical_coordinate": item["donor_count"],
        "checks": ["target and donor are different facility records",
                   "distinctive normalized names exactly equal", "cities exactly equal",
                   "states exactly equal", "all rooftop donors agree on one coordinate"],
    }, sort_keys=True)
    a = assertion(target["facility_id"], "lat_lon", f"{lat:.6f},{lon:.6f}",
                  source_id=SOURCE, basis="rooftop", confidence=0.95,
                  evidence=workflow + " :: " + document)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ev, fact = _rows_for(a, target["release_tag"], now[:10], now)
    db.execute("INSERT INTO dim_source(source_key,source_id,name,class,status) VALUES(%s,%s,%s,'enrichment','active') ON CONFLICT(source_key) DO NOTHING",
               (SOURCE, SOURCE, "Internal exact-site verified-rooftop crossmatch"))
    db.execute("INSERT INTO ref_source_row(row_hash,source_key,source_url,source_document,retrieved_date,facility_key,match_method,match_confidence,last_seen_release) VALUES(" + ",".join(["%s"] * 9) + ") ON CONFLICT(row_hash) DO NOTHING", ev)
    inserted = len(db.execute("INSERT INTO fact_assertions(assertion_id,release_tag,facility_key,source_key,field_key,date_key,value,basis,site_visit,row_hash,confidence,source_class,asserted_at) VALUES(" + ",".join(["%s"] * 13) + ") ON CONFLICT(assertion_id,release_tag) DO NOTHING RETURNING assertion_id", fact).fetchall())
    saved = db.execute("SELECT a.value,a.basis,r.source_url FROM fact_assertions a JOIN ref_source_row r ON r.row_hash=a.row_hash WHERE a.assertion_id=%s AND a.release_tag=%s", (fact[0], target["release_tag"])).fetchone()
    if not saved or saved["value"] != a["value"] or saved["basis"] != "rooftop" or saved["source_url"] != workflow:
        raise RuntimeError("Internal rooftop crossmatch provenance verification failed")
    db.execute("UPDATE coordinate_recovery_rows SET status='rooftop_asserted' WHERE campaign_id=%s AND facility_id=%s", (campaign, target["facility_id"]))
    return inserted


def run(db, campaign: str, workflow: str, write: bool = False, limit: int = 100) -> dict:
    targets = db.execute("""SELECT c.facility_id,c.baseline->>'name' AS name,
            c.baseline->>'city' AS city,c.baseline->>'state' AS state,g.release_tag
        FROM coordinate_recovery_rows c JOIN golden_facility g ON g.facility_key=c.facility_id
        WHERE c.campaign_id=%s AND c.status='unresolved' ORDER BY c.facility_id""",
        (campaign,)).fetchall()
    donors = db.execute("""SELECT g.facility_key AS facility_id,g.name,g.city,g.state,
            a.assertion_id,a.value,a.source_key,a.row_hash,r.source_url
        FROM golden_facility g JOIN fact_assertions a ON a.facility_key=g.facility_key
        LEFT JOIN ref_source_row r ON r.row_hash=a.row_hash
        WHERE a.field_key='lat_lon' AND a.basis='rooftop'
          AND NULLIF(btrim(a.value),'') IS NOT NULL""").fetchall()
    selected, refused = choose([dict(r) for r in targets], [dict(r) for r in donors])
    selected = selected[:limit]
    inserted = 0
    if write:
        for item in selected:
            inserted += _append(db, campaign, item, workflow)
    return {"unresolved_rows_scanned": len(targets), "rooftop_donor_assertions": len(donors),
            "unambiguous_exact_site_matches": len(selected), "refused": dict(refused),
            "write_requested": write, "coordinate_assertions_inserted": inserted,
            "new_api_calls": 0, "api_cost_usd": 0.0, "external_data_sent": False}
