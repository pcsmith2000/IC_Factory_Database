"""Buildings: which Overture buildings make up a facility, and its square footage from them.

The footprint stage (pipeline/enrich/footprint.py, stage 11) measures the LARGEST Overture building
within 30m of a coordinate. That was the right call while coordinates were street geocodes. With
rooftop coordinates it measures a neighbour as often as not: of 740 measured facilities, the point
sat inside the measured building for 427, and 261 measured a building more than 10m away.

This module attaches buildings to a facility instead, one judgement per facility, against its
current golden coordinate:

  * exactly one building contains the point -> attached as the facility's primary building
    (status `confirmed`, basis `contains_point`), and the facility's building_sqft is its area;
  * no building contains it -> the largest building within PROPOSE_RADIUS_M is `proposed`
    (basis `nearest_largest`): shown dashed, not counted, waiting for a person or an agent;
  * several contain it (overlapping or nested outlines) -> the largest is `proposed` (`ambiguous`);
  * nothing within PROPOSE_RADIUS_M -> nothing attached.

Every other building within CANDIDATE_RADIUS_M is stored as a `candidate`, with its outline, so the
map can offer it for attaching without reading S3. Every outcome but `contains_point` lands in
facility_building_review, which is the queue people and agents work through.

A person's or an agent's decision is never overridden: once any of a facility's buildings carries
one, the pipeline only refreshes that facility's candidates (outcome `held`). At most
MAX_CONFIRMED buildings count toward a facility.

The square footage is asserted like any other fact (source `facility_buildings`), so survivorship,
provenance and the evidence panel work unchanged; registry/survivorship.yaml ranks
basis:buildings_contains_point above stage 11's basis:footprint, which stays as the fallback.

    python -m pipeline.buildings [--limit 500] [--max-files 20] [--refresh] [--dry-run]
"""
from __future__ import annotations
import hashlib, json, sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = "facility_buildings"
CANDIDATE_RADIUS_M = 150.0      # a plant campus: the buildings a person may want to attach
PROPOSE_RADIUS_M = 30.0         # the old stage-11 radius: a rooftop geocode can sit just outside
MIN_CANDIDATE_SQFT = 1500       # sheds and garages are not plant capacity
MAX_CANDIDATES = 20
MAX_CONFIRMED = 5
# A building under this that contains the coordinate is the office, guard house or a house on the
# site, not the plant. Measured on the 186 facilities a source states a plant size for: all 16 such
# attachments were under half the stated size (Vaagen Timbers 1,082 sqft against 70,000; Clark
# Pacific 6,688 against 120,000), so they are proposed for review rather than attached.
SMALL_SQFT = 10_000
HUMAN = ("person", "agent")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _h(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


def decide(point: str, buildings: list[dict], existing: dict[str, dict]) -> dict:
    """One facility's judgement. Pure: no database, no S3.

    buildings: footprint.around() output for this point, nearest first.
    existing: building_id -> its facility_building row, if any.
    Returns {"outcome", "reason", "rows": {building_id: status/role/basis/...}, "drop": [ids]}.
    `rows` holds every row to upsert; `drop` the pipeline's own candidate rows no longer nearby.
    """
    held = any(r["decided_kind"] in HUMAN for r in existing.values())
    near = {b["building_id"]: b for b in buildings}
    rows: dict[str, dict] = {}

    def row(b, status, role=None, basis=None):
        return {"status": status, "role": role, "basis": basis, "distance_m": b["distance_m"],
                "contains_point": int(b["contains_point"]), "point": point}

    if held:
        # Refresh what is nearby; touch nothing a person or an agent decided.
        for bid, b in near.items():
            if bid not in existing:
                rows[bid] = row(b, "candidate")
        drop = [bid for bid, r in existing.items()
                if r["decided_kind"] == "pipeline" and r["status"] == "candidate" and bid not in near]
        return {"outcome": "held", "reason": "a person or an agent has decided this facility's buildings",
                "rows": rows, "drop": drop}

    containing = [b for b in buildings if b["contains_point"]]
    if len(containing) == 1 and containing[0]["area_sqft"] >= SMALL_SQFT:
        pick, status, basis, outcome, reason = containing[0], "confirmed", "contains_point", "contains_point", ""
    elif len(containing) == 1:
        # The point is in a small building. Propose the largest building within 30m (often the
        # plant right behind the office), or the small one itself if nothing bigger is that close.
        small = containing[0]
        close = [b for b in buildings if b["distance_m"] <= PROPOSE_RADIUS_M]
        pick = max(close + [small], key=lambda b: b["area_sqft"])
        status, basis, outcome = "proposed", "small_building", "small_building"
        reason = (f"the point is in a {small['area_sqft']:,} sqft building, under the {SMALL_SQFT:,} sqft a plant "
                  f"is taken to need; " + ("it is proposed" if pick is small else
                  f"the largest within {PROPOSE_RADIUS_M:.0f}m ({pick['area_sqft']:,} sqft, "
                  f"{pick['distance_m']}m away) is proposed"))
    elif containing:
        pick = max(containing, key=lambda b: b["area_sqft"])
        status, basis, outcome = "proposed", "ambiguous", "ambiguous"
        reason = f"{len(containing)} Overture buildings contain the point; the largest is proposed"
    else:
        close = [b for b in buildings if b["distance_m"] <= PROPOSE_RADIUS_M]
        pick = max(close, key=lambda b: b["area_sqft"]) if close else None
        if pick:
            status, basis, outcome = "proposed", "nearest_largest", "nearest_largest"
            reason = (f"no building contains the point; the largest within {PROPOSE_RADIUS_M:.0f}m "
                      f"({pick['distance_m']}m away) is proposed")
        else:
            status = basis = None
            outcome = "none"
            reason = f"no Overture building contains the point or lies within {PROPOSE_RADIUS_M:.0f}m"
    for bid, b in near.items():
        rows[bid] = row(b, "candidate")
    if pick:
        rows[pick["building_id"]] = row(pick, status, "primary", basis)
    drop = [bid for bid, r in existing.items() if bid not in near and r["status"] != "rejected"]
    return {"outcome": outcome, "reason": reason, "rows": rows, "drop": drop}


def confirmed_sqft(rows: dict[str, dict], areas: dict[str, int]) -> tuple[int | None, list[str]]:
    ids = sorted(bid for bid, r in rows.items() if r["status"] == "confirmed")[:MAX_CONFIRMED]
    return (sum(areas[b] for b in ids) if ids else None), ids


def plan(wh, *, limit: int, refresh: bool, only: set[str] | None = None) -> list[dict]:
    """Facilities to judge: golden rows with a coordinate whose point (or Overture release) has
    not been judged yet, or every one of them with `refresh`; `only` narrows it to these ids."""
    from .enrich.footprint import DEFAULT_RELEASE
    done = {} if refresh else {
        r["facility_key"]: (r["point"], r["overture_release"])
        for r in wh.query("SELECT facility_key, point, overture_release FROM facility_building_review")}
    out = []
    for r in wh.query("SELECT facility_key, lat_lon FROM golden_facility WHERE lat_lon IS NOT NULL "
                      "ORDER BY facility_key"):
        try:
            lat, lon = (float(x) for x in r["lat_lon"].split(","))
        except (ValueError, AttributeError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0.0, 0.0):
            continue
        if only is not None and r["facility_key"] not in only:
            continue
        if done.get(r["facility_key"]) == (r["lat_lon"], DEFAULT_RELEASE):
            continue
        out.append({"facility_id": r["facility_key"], "lat": lat, "lon": lon, "point": r["lat_lon"]})
        if len(out) >= limit:
            break
    return out


def apply(wh, measured: list[dict], *, dry_run: bool) -> dict:
    """Judge each measured point and write the outcome. One transaction per facility, so a run
    that dies part-way leaves every facility it finished complete and the rest untouched."""
    from .golden_refresh import current_release
    tag = current_release(wh)
    now, today = _now(), date.today().isoformat()
    report = {"judged": 0, "deferred": 0, "contains_point": 0, "nearest_largest": 0, "ambiguous": 0,
              "none": 0, "held": 0, "small_building": 0, "sqft_asserted": 0, "dry_run": dry_run, "examples": [],
              "judgments": []}
    # Every facility's current rows in one read, not one query per facility: against Neon each
    # round trip costs more than the work, and the first full run spent 34 minutes writing 1,268.
    known: dict[str, dict] = {}
    fids = sorted({m["facility_id"] for m in measured if not m.get("reason")})
    for i in range(0, len(fids), 500):
        part = fids[i:i + 500]
        for r in wh.query(f"SELECT * FROM facility_building WHERE facility_key IN ({','.join('?' * len(part))})",
                          tuple(part)):
            known.setdefault(r["facility_key"], {})[r["building_id"]] = r
    batch = _new_batch()
    for m in measured:
        if m.get("reason"):                          # outside every file, or past the file ceiling
            report["deferred"] += 1
            continue
        fid, point, release = m["facility_id"], m["point"], m["overture_release"]
        existing = known.get(fid, {})
        d = decide(point, m["buildings"], existing)
        report["judged"] += 1
        report[d["outcome"]] += 1
        areas = {b["building_id"]: b["area_sqft"] for b in m["buildings"]}
        before_sqft, before_ids = confirmed_sqft(existing, {b: 0 for b in existing})
        merged = {**{b: r for b, r in existing.items() if b not in d["drop"]}, **d["rows"]}
        sqft, ids = confirmed_sqft(merged, {**{b: 0 for b in merged}, **areas})
        assert_sqft = d["outcome"] == "contains_point" and sqft and ids != before_ids
        if before_ids and d["outcome"] != "contains_point" and d["outcome"] != "held":
            # An assertion cannot be withdrawn: the earlier square footage keeps winning until a
            # person confirms a building. Say so where they will look.
            d["reason"] += (f"; the point no longer lies in {'+'.join(before_ids)}, whose square footage "
                            f"stays in golden until a person confirms a building")
        pick = next((bid for bid, r in d["rows"].items() if r["role"] == "primary"), None)
        near30 = [b for b in m["buildings"] if b["distance_m"] <= PROPOSE_RADIUS_M]
        by_id = {b["building_id"]: b for b in m["buildings"]}
        report["judgments"].append({
            "facility_id": fid, "point": point, "outcome": d["outcome"],
            "status": d["rows"][pick]["status"] if pick else None,
            "building_id": pick, "area_sqft": by_id[pick]["area_sqft"] if pick else None,
            "distance_m": by_id[pick]["distance_m"] if pick else None,
            "largest_30m_sqft": max((b["area_sqft"] for b in near30), default=None),
            "n_candidates": len(m["buildings"]),
            "geometry": by_id[pick]["geometry"] if pick else None})
        if len(report["examples"]) < 20:
            report["examples"].append({"facility_id": fid, "outcome": d["outcome"], "sqft": sqft,
                                       "buildings": ids, "reason": d["reason"]})
        if dry_run:
            report["sqft_asserted"] += 1 if assert_sqft else 0
            continue
        # Written in batches of WRITE_BATCH facilities, one transaction and one round trip per kind
        # of row each: a run that dies loses at most the batch in flight, which rolls back whole,
        # and the next run judges those facilities again because their review row never landed.
        batch["footprints"].update({b["building_id"]: (
            b["building_id"], release, json.dumps(b["geometry"], separators=(",", ":")),
            b["area_sqft"], b["height_m"], b["centroid"], now) for b in m["buildings"]})
        for bid in d["drop"]:
            old = existing[bid]
            batch["drops"].append((fid, bid))
            if old["status"] != "candidate":
                batch["events"].append(_event_row(fid, bid, old["status"], "candidate",
                                                  "the point moved away from this building", now))
        for bid, r in d["rows"].items():
            old = existing.get(bid)
            batch["rows"].append((fid, bid, r["status"], r["role"], r["basis"], r["distance_m"],
                                  r["contains_point"], r["point"], now))
            if r["status"] != "candidate" and (old is None or old["status"] != r["status"]):
                batch["events"].append(_event_row(fid, bid, old["status"] if old else None, r["status"],
                                                  d["reason"] or r["basis"], now))
        batch["reviews"].append((fid, point, release, d["outcome"], d["reason"] or None, len(m["buildings"]), now))
        if assert_sqft:
            rh = _h(SOURCE, fid, ",".join(ids), release)
            doc = f"{release}:{'+'.join(ids)} contains {point}"
            batch["refs"].append((rh, SOURCE, None, doc, today, "building_sqft", "pipeline/buildings.py",
                                  None, None, None, None, None, fid, "contains_point", None, tag))
            batch["facts"].append((_h(SOURCE, "assertion", fid, "building_sqft", sqft, rh), tag, fid, SOURCE,
                                   "building_sqft", today, str(sqft), "buildings_contains_point", 0, rh, None,
                                   "enrichment", now))
            report["sqft_asserted"] += 1
        batch["n"] += 1
        if batch["n"] >= WRITE_BATCH:
            _flush(wh, batch, today)
            batch = _new_batch()
    if not dry_run:
        _flush(wh, batch, today)
    return report


WRITE_BATCH = 200


def _event_row(fid, bid, before, after, reason, at, actor_kind="pipeline", actor="pipeline/buildings.py"):
    return (_h("event", fid, bid, before, after, at), fid, bid, before, after, actor_kind, actor, reason, at)


def _new_batch() -> dict:
    return {"n": 0, "footprints": {}, "drops": [], "rows": [], "events": [], "reviews": [], "refs": [], "facts": []}


def _flush(wh, b: dict, today: str) -> None:
    """One transaction for a batch of facilities: every kind of row in one executemany."""
    from .warehouse import SYNTHETIC_SOURCES, _date_row
    if not b["n"]:
        return
    with wh.transaction() as c:
        c.executemany("INSERT INTO building_footprint VALUES (?,?,?,?,?,?,?) ON CONFLICT (building_id) DO UPDATE "
                      "SET overture_release = excluded.overture_release, geometry = excluded.geometry, "
                      "area_sqft = excluded.area_sqft, height_m = excluded.height_m, "
                      "centroid = excluded.centroid, fetched_at = excluded.fetched_at", list(b["footprints"].values()))
        c.executemany("DELETE FROM facility_building WHERE facility_key = ? AND building_id = ?", b["drops"])
        c.executemany("INSERT INTO facility_building (facility_key, building_id, status, role, basis, distance_m, "
                      "contains_point, point, decided_kind, decided_by, decided_at) "
                      "VALUES (?,?,?,?,?,?,?,?,'pipeline','pipeline/buildings.py',?) "
                      "ON CONFLICT (facility_key, building_id) DO UPDATE SET status = excluded.status, "
                      "role = excluded.role, basis = excluded.basis, distance_m = excluded.distance_m, "
                      "contains_point = excluded.contains_point, point = excluded.point, "
                      "decided_at = excluded.decided_at", b["rows"])
        c.executemany("INSERT INTO facility_building_event VALUES (?,?,?,?,?,?,?,?,?) "
                      "ON CONFLICT (event_id) DO NOTHING", b["events"])
        c.executemany("INSERT INTO facility_building_review VALUES (?,?,?,?,?,?,?) ON CONFLICT (facility_key) DO UPDATE "
                      "SET point = excluded.point, overture_release = excluded.overture_release, "
                      "outcome = excluded.outcome, reason = excluded.reason, "
                      "n_candidates = excluded.n_candidates, evaluated_at = excluded.evaluated_at", b["reviews"])
        # A reviewer who moved the pin parked the facility (queue done, verdict move_pin) until it was
        # judged at the new point. This is that judgement: hand it back to the agent's queue.
        c.executemany("UPDATE facility_review_task SET queue = 'agent', updated_at = ? "
                      "WHERE facility_key = ? AND queue = 'done' AND last_verdict = 'move_pin'",
                      [(r[6], r[0]) for r in b["reviews"]])
        if b["facts"]:
            c.execute("INSERT INTO dim_source VALUES (?,?,?,?,?,?,?) ON CONFLICT (source_key) DO NOTHING",
                      (SOURCE, SOURCE, SYNTHETIC_SOURCES[SOURCE]["name"], "enrichment", "overture_buildings",
                       None, "active"))
            if dr := _date_row(today):
                c.execute("INSERT INTO dim_date VALUES (?,?,?,?) ON CONFLICT (date_key) DO NOTHING", dr)
            c.executemany("INSERT INTO ref_source_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT (row_hash) DO NOTHING", b["refs"])
            c.executemany("INSERT INTO fact_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT (assertion_id, release_tag) DO NOTHING", b["facts"])


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    from .warehouse import open_warehouse, SqliteWarehouse
    from .enrich import footprint
    ap = argparse.ArgumentParser(prog="python -m pipeline.buildings")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    ap.add_argument("--limit", type=int, default=500, help="facilities per run")
    ap.add_argument("--max-files", type=int, default=20, help="Overture parquet files read per run (~1 min each)")
    ap.add_argument("--refresh", action="store_true", help="re-judge facilities already judged at this point")
    ap.add_argument("--dry-run", action="store_true", help="read Overture and report; write nothing")
    ap.add_argument("--index", default="enrich/overture_index.json", help="cached Overture file -> bbox index")
    ap.add_argument("--report", default=None, help="write one JSON line per judged facility here")
    ap.add_argument("--only", default="", help="comma-separated facility ids to judge (a test sample)")
    ap.add_argument("--only-with-stated-sqft", action="store_true",
                    help="judge only facilities a source states a plant size for (ground truth for a test)")
    args = ap.parse_args(argv)
    def connect():
        return (SqliteWarehouse(Path(args.db)) if args.db
                else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    wh = connect()
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    only = None
    if args.only:
        only = set(args.only.replace(",", " ").split())
        if args.only_with_stated_sqft:
            raise SystemExit("--only and --only-with-stated-sqft are exclusive")
    elif args.only_with_stated_sqft:
        # The facilities a source states a plant size for: the ground truth to test a run against.
        only = {r["facility_key"] for r in wh.query(
            "SELECT facility_key FROM golden_facility WHERE sq_ft IS NOT NULL AND lat_lon IS NOT NULL")}
    todo = plan(wh, limit=args.limit, refresh=args.refresh, only=only)
    # Reading Overture takes about a minute per file, and a connection left idle that long is
    # closed under us: Neon suspends an idle compute after five minutes and drops its connections
    # (the first run died on the next query with AdminShutdown after 12 minutes of S3 reads).
    # Close it now and open a fresh one for the writes.
    wh.close()
    measured = footprint.around(todo, radius_m=CANDIDATE_RADIUS_M, min_sqft=MIN_CANDIDATE_SQFT,
                                max_n=MAX_CANDIDATES, cache=Path(args.index), max_files=args.max_files)
    wh = connect()
    rep = apply(wh, measured, dry_run=args.dry_run)
    wh.close()
    judgments = rep.pop("judgments")
    if args.report:
        # Every judgment, with the chosen building's outline: what a reviewer checks the run against.
        Path(args.report).write_text("\n".join(json.dumps(j, separators=(",", ":")) for j in judgments) + "\n")
    rep["planned"] = len(todo)
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
