"""Stage 11 — the building under a rooftop coordinate, and its area.

Overture publishes buildings as 512 GeoParquet files on S3. Filtering them by bbox in one query
does not work: a predicate covering many scattered points matches nearly every file, so the scan
reads all of them. Thirty points took over half an hour and did not finish.

Each file covers a contiguous region, though, and parquet footers carry per-file bbox statistics.
Reading those footers once (about 110s for the release) gives a file -> bbox index, after which a
point resolves to a single file. The same thirty points then take 59 seconds, and the cost scales
with the number of distinct files touched rather than with the number of facilities.

Area is computed from the ring coordinates. DuckDB's ST_Area_Spheroid must not be used: it ignores
the convergence of meridians and returns the same area for one polygon at every latitude — correct
at the equator, 21% high at 34N and 99% high at 60N. Silent, and systematic in a national dataset.
"""
from __future__ import annotations
import json, math, re
from pathlib import Path

R = 6371008.8                  # IUGG mean radius
M2_FT2 = 10.7639104
MATCH_RADIUS_M = 30.0          # a rooftop geocode can sit just outside the polygon it names
DEFAULT_RELEASE = "2026-08-19.0"
BUILDINGS = ("s3://overturemaps-us-west-2/release/{release}"
             "/theme=buildings/type=building/*.parquet")


def _connect():
    import duckdb
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("CREATE OR REPLACE SECRET ov (TYPE s3, PROVIDER config, REGION 'us-west-2');")
    return con


def build_index(release: str = DEFAULT_RELEASE, cache: Path | None = None) -> list[dict]:
    """file -> bbox, from parquet footers only. Cache it: it is fixed for an Overture release."""
    if cache and cache.exists():
        return json.loads(cache.read_text())
    con = _connect()
    # the metadata path separator is ", ", not "." — 'bbox.xmin' silently matches nothing
    rows = con.execute(f"""
        SELECT file_name,
               min(CASE WHEN path_in_schema='bbox, xmin' THEN CAST(stats_min_value AS DOUBLE) END) AS xmin,
               max(CASE WHEN path_in_schema='bbox, xmax' THEN CAST(stats_max_value AS DOUBLE) END) AS xmax,
               min(CASE WHEN path_in_schema='bbox, ymin' THEN CAST(stats_min_value AS DOUBLE) END) AS ymin,
               max(CASE WHEN path_in_schema='bbox, ymax' THEN CAST(stats_max_value AS DOUBLE) END) AS ymax
        FROM parquet_metadata('{BUILDINGS.format(release=release)}')
        GROUP BY file_name""").fetchall()
    idx = [{"file": f, "xmin": a, "xmax": b, "ymin": c, "ymax": d}
           for f, a, b, c, d in rows if a is not None]
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(idx))
    return idx


def file_for(idx: list[dict], lat: float, lon: float) -> str | None:
    for e in idx:
        if e["xmin"] <= lon <= e["xmax"] and e["ymin"] <= lat <= e["ymax"]:
            return e["file"]
    return None


def _ring_m2(ring: list[tuple[float, float]]) -> float:
    """Shoelace on a local equirectangular projection about the ring's own centroid."""
    lat0 = sum(p[1] for p in ring) / len(ring)
    lon0 = sum(p[0] for p in ring) / len(ring)
    k = math.cos(math.radians(lat0))
    pts = [(math.radians(x - lon0) * k * R, math.radians(y - lat0) * R) for x, y in ring]
    s = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
            for i in range(len(pts)))
    return abs(s) / 2.0


def wkt_area_m2(wkt: str) -> float:
    """Outer ring minus holes, over every polygon of a POLYGON or MULTIPOLYGON."""
    total = 0.0
    for poly in re.findall(r"\(\(.*?\)\)", wkt, re.S):
        for j, ring in enumerate(re.findall(r"\(([^()]*)\)", poly)):
            pts = [tuple(map(float, p.split()[:2])) for p in ring.split(",")]
            a = _ring_m2(pts)
            total += a if j == 0 else -a
    return total


def measure(points: list[dict], release: str = DEFAULT_RELEASE,
            cache: Path | None = None, box_deg: float = 0.0035) -> list[dict]:
    """points: [{facility_id, lat, lon}] -> one result each, grouped by file so each is read once."""
    idx = build_index(release, cache)
    by_file: dict[str, list[dict]] = {}
    out: list[dict] = []
    for p in points:
        f = file_for(idx, p["lat"], p["lon"])
        if f is None:
            out.append({**p, "building_sqft": None, "reason": "outside every Overture file bbox"})
        else:
            by_file.setdefault(f, []).append(p)
    con = _connect()
    for f, ps in by_file.items():
        where = " OR ".join(f"(bbox.xmin BETWEEN {p['lon']-box_deg} AND {p['lon']+box_deg} AND "
                            f"bbox.ymin BETWEEN {p['lat']-box_deg} AND {p['lat']+box_deg})" for p in ps)
        con.execute(f"CREATE OR REPLACE TEMP VIEW src AS "
                    f"SELECT * FROM read_parquet('{f}') WHERE {where}")
        for p in ps:
            cands = con.execute(
                "SELECT ST_AsText(geometry), ST_Distance(geometry, ST_Point(?, ?)), id, height "
                "FROM src WHERE bbox.xmin BETWEEN ? AND ? AND bbox.ymin BETWEEN ? AND ? "
                "ORDER BY 2", [p["lon"], p["lat"],
                               p["lon"]-box_deg, p["lon"]+box_deg,
                               p["lat"]-box_deg, p["lat"]+box_deg]).fetchall()
            m_per_deg = math.pi * R / 180
            near = [c for c in cands if c[1] * m_per_deg <= MATCH_RADIUS_M]
            if not near:
                out.append({**p, "building_sqft": None, "n_nearby": len(cands),
                            "reason": f"no Overture building within {MATCH_RADIUS_M:.0f}m"})
                continue
            wkt, dist, bid, height = near[0]
            out.append({**p, "building_sqft": round(wkt_area_m2(wkt) * M2_FT2),
                        "building_id": bid, "offset_m": round(dist * m_per_deg, 1),
                        "height_m": height, "n_within_radius": len(near),
                        "overture_release": release})
    return out
