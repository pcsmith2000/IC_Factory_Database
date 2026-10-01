"""pipeline/buildings.py: a facility's buildings are the ones its coordinate is in, not the ones near it."""
import json
from pathlib import Path

import pytest

from pipeline import buildings as B
from pipeline import warehouse
from pipeline.golden import build_golden
from pipeline.registry import load_yaml

RULES = load_yaml(Path(__file__).resolve().parents[1] / "registry" / "survivorship.yaml")
POINT = "35.000000,-80.000000"


def bld(bid, dist, area, inside=False):
    ring = [[-80.0, 35.0], [-80.001, 35.0], [-80.001, 35.001], [-80.0, 35.001], [-80.0, 35.0]]
    return {"building_id": bid, "geometry": {"type": "Polygon", "coordinates": [ring]}, "area_sqft": area,
            "height_m": None, "centroid": "35.0005,-80.0005", "distance_m": dist, "contains_point": inside}


def existing(bid, status, kind="pipeline"):
    return {bid: {"building_id": bid, "status": status, "decided_kind": kind}}


def test_the_one_building_that_contains_the_point_is_attached_and_its_neighbours_are_only_candidates():
    d = B.decide(POINT, [bld("plant", 0, 90000, True), bld("office", 12, 4000), bld("warehouse", 60, 150000)], {})
    assert d["outcome"] == "contains_point" and d["reason"] == ""
    assert d["rows"]["plant"]["status"] == "confirmed" and d["rows"]["plant"]["role"] == "primary"
    assert {d["rows"][b]["status"] for b in ("office", "warehouse")} == {"candidate"}


def test_no_containing_building_proposes_the_largest_within_30m_and_counts_nothing():
    d = B.decide(POINT, [bld("office", 8, 4000), bld("plant", 25, 90000), bld("far", 80, 300000)], {})
    assert d["outcome"] == "nearest_largest"
    assert d["rows"]["plant"]["status"] == "proposed" and d["rows"]["far"]["status"] == "candidate"
    assert B.confirmed_sqft(d["rows"], {"office": 4000, "plant": 90000, "far": 300000}) == (None, [])


def test_overlapping_containing_outlines_are_ambiguous_and_nothing_nearby_is_none():
    d = B.decide(POINT, [bld("a", 0, 5000, True), bld("b", 0, 70000, True)], {})
    assert d["outcome"] == "ambiguous" and d["rows"]["b"]["status"] == "proposed"
    assert B.decide(POINT, [bld("far", 90, 50000)], {})["outcome"] == "none"


def test_a_person_s_decision_is_never_overridden():
    ex = {**existing("plant", "confirmed", "person"), **existing("stale", "candidate")}
    d = B.decide(POINT, [bld("office", 0, 4000, True), bld("new", 40, 9000)], ex)
    assert d["outcome"] == "held"
    assert set(d["rows"]) == {"office", "new"} and all(r["status"] == "candidate" for r in d["rows"].values())
    assert d["drop"] == ["stale"]                       # the pipeline's own stale candidate only


@pytest.fixture
def wh(tmp_path, monkeypatch):
    w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    w.init_schema()
    with w.transaction() as c:
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, lat_lon) VALUES "
                  "('IC-1', 'r1', 'Plant', ?), ('IC-2', 'r1', 'Other', '36.0,-81.0'), ('IC-3', 'r1', 'No coords', NULL)",
                  (POINT,))
    yield w
    w.close()


def measured(fid, point, blds):
    return {"facility_id": fid, "point": point, "lat": 0, "lon": 0, "buildings": blds,
            "reason": "", "overture_release": "rel"}


def test_apply_writes_buildings_events_review_and_one_square_footage_assertion(wh):
    m = [measured("IC-1", POINT, [bld("plant", 0, 90000, True), bld("office", 12, 4000)]),
         measured("IC-2", "36.0,-81.0", [bld("shed", 20, 2000)]),
         {"facility_id": "IC-9", "buildings": [], "reason": "deferred: file ceiling reached"}]
    rep = B.apply(wh, m, dry_run=False)
    assert (rep["judged"], rep["deferred"], rep["contains_point"], rep["nearest_largest"], rep["sqft_asserted"]) == (2, 1, 1, 1, 1)
    fb = {(r["facility_key"], r["building_id"]): r["status"] for r in wh.query("SELECT * FROM facility_building")}
    assert fb == {("IC-1", "plant"): "confirmed", ("IC-1", "office"): "candidate", ("IC-2", "shed"): "proposed"}
    geo = wh.query("SELECT geometry, area_sqft FROM building_footprint WHERE building_id = 'plant'")[0]
    assert json.loads(geo["geometry"])["type"] == "Polygon" and geo["area_sqft"] == 90000
    review = {r["facility_key"]: r["outcome"] for r in wh.query("SELECT * FROM facility_building_review")}
    assert review == {"IC-1": "contains_point", "IC-2": "nearest_largest"}
    ev = wh.query("SELECT facility_key, building_id, status_after FROM facility_building_event ORDER BY facility_key")
    assert [(e["facility_key"], e["status_after"]) for e in ev] == [("IC-1", "confirmed"), ("IC-2", "proposed")]
    fa = wh.query("SELECT value, basis, source_key FROM fact_assertions WHERE field_key = 'building_sqft'")
    assert fa == [{"value": "90000", "basis": "buildings_contains_point", "source_key": "facility_buildings"}]
    assert wh.query("SELECT facility_key FROM golden_dirty") == [{"facility_key": "IC-1"}]

    # The same judgement again writes no second assertion and no second event.
    B.apply(wh, m[:1], dry_run=False)
    assert len(wh.query("SELECT 1 FROM fact_assertions WHERE field_key = 'building_sqft'")) == 1
    assert len(wh.query("SELECT 1 FROM facility_building_event")) == 2


def test_a_moved_point_releases_the_old_building_and_says_its_square_footage_stays(wh):
    B.apply(wh, [measured("IC-1", POINT, [bld("plant", 0, 90000, True)])], dry_run=False)
    rep = B.apply(wh, [measured("IC-1", "35.01,-80.0", [bld("yard", 20, 30000)])], dry_run=False)
    assert rep["nearest_largest"] == 1
    assert {r["building_id"]: r["status"] for r in wh.query("SELECT * FROM facility_building")} == {"yard": "proposed"}
    reason = wh.query("SELECT reason FROM facility_building_review")[0]["reason"]
    assert "no longer lies in plant" in reason and "stays in golden" in reason
    ev = [(e["building_id"], e["status_before"], e["status_after"])
          for e in wh.query("SELECT * FROM facility_building_event ORDER BY at, building_id")]
    assert ("plant", "confirmed", "candidate") in ev


def test_dry_run_writes_nothing_and_plan_skips_points_already_judged(wh):
    from pipeline.enrich.footprint import DEFAULT_RELEASE
    todo = B.plan(wh, limit=10, refresh=False)
    assert [t["facility_id"] for t in todo] == ["IC-1", "IC-2"]          # IC-3 has no coordinate
    m = [{**measured("IC-1", POINT, [bld("plant", 0, 90000, True)]), "overture_release": DEFAULT_RELEASE}]
    assert B.apply(wh, m, dry_run=True)["sqft_asserted"] == 1
    assert wh.query("SELECT count(*) AS n FROM facility_building")[0]["n"] == 0
    B.apply(wh, m, dry_run=False)
    assert [t["facility_id"] for t in B.plan(wh, limit=10, refresh=False)] == ["IC-2"]
    assert [t["facility_id"] for t in B.plan(wh, limit=10, refresh=True)] == ["IC-1", "IC-2"]


def a(field, value, source, basis, date="2026-09-01"):
    return {"facility_id": "IC-1", "field": field, "value": value, "source_id": source, "source_class": "enrichment",
            "basis": basis, "retrieved_date": date, "site_visit": False, "row_hash": "", "confidence": 1}


def test_survivorship_the_containing_building_beats_the_nearby_largest_even_when_older():
    rows, _ = build_golden([a("building_sqft", "300000", "overture:building", "footprint", "2026-09-20"),
                            a("building_sqft", "90000", "facility_buildings", "buildings_contains_point", "2026-09-01")], RULES)
    assert rows[0]["building_sqft"] == "90000" and rows[0]["floor_area_sqft"] == "90000"
    rows, _ = build_golden([a("building_sqft", "300000", "overture:building", "footprint")], RULES)
    assert rows[0]["building_sqft"] == "300000"            # the old measure still fills a hole
    rows, _ = build_golden([a("building_sqft", "90000", "facility_buildings", "buildings_contains_point", "2026-09-30"),
                            a("building_sqft", "130000", "facility_buildings", "buildings_confirmed", "2026-09-01")], RULES)
    assert rows[0]["building_sqft"] == "130000"            # a person's set beats the pipeline's


def test_main_reconnects_after_reading_overture(tmp_path, monkeypatch):
    # A connection held through ~1 minute per Overture file is closed under us by an idle-suspending
    # database; main() must read the plan, close, measure, and write on a fresh connection.
    from pipeline.enrich import footprint
    db = tmp_path / "w.sqlite"
    w = warehouse.SqliteWarehouse(db); w.init_schema()
    with w.transaction() as c:
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, lat_lon) VALUES ('IC-1','r1','P',?)", (POINT,))
    w.close()
    opened, closed = [], []
    real = warehouse.SqliteWarehouse

    class Tracked(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); opened.append(self)

        def close(self):
            closed.append(self); super().close()

    def fake_around(points, **kw):
        assert opened and all(o in closed for o in opened), "the warehouse must be closed while Overture is read"
        return [{**p, "buildings": [bld("plant", 0, 90000, True)], "reason": "", "overture_release": "rel"} for p in points]

    monkeypatch.setattr(warehouse, "SqliteWarehouse", Tracked)
    monkeypatch.setattr(footprint, "around", fake_around)
    assert B.main(["--db", str(db)]) == 0
    assert len(opened) == 2 and len(closed) == 2
    w = real(db)
    assert w.query("SELECT status FROM facility_building") == [{"status": "confirmed"}]
    w.close()


def test_report_writes_one_line_per_judged_facility(tmp_path, monkeypatch):
    from pipeline.enrich import footprint
    db = tmp_path / "w.sqlite"
    w = warehouse.SqliteWarehouse(db); w.init_schema()
    with w.transaction() as c:
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, lat_lon) VALUES "
                  "('IC-1','r1','P',?), ('IC-2','r1','Q','36.0,-81.0')", (POINT,))
    w.close()
    monkeypatch.setattr(footprint, "around", lambda pts, **kw: [
        {**p, "buildings": [bld("plant", 0, 90000, True), bld("office", 12, 4000)] if p["facility_id"] == "IC-1"
         else [bld("shed", 20, 2000)], "reason": "", "overture_release": "rel"} for p in pts])
    out = tmp_path / "j.jsonl"
    assert B.main(["--db", str(db), "--dry-run", "--report", str(out)]) == 0
    rows = {j["facility_id"]: j for j in map(json.loads, out.read_text().splitlines())}
    assert rows["IC-1"]["outcome"] == "contains_point" and rows["IC-1"]["area_sqft"] == 90000
    assert rows["IC-1"]["largest_30m_sqft"] == 90000 and rows["IC-1"]["geometry"]["type"] == "Polygon"
    assert rows["IC-2"]["status"] == "proposed" and rows["IC-2"]["distance_m"] == 20


def test_only_narrows_the_plan(wh):
    assert [t["facility_id"] for t in B.plan(wh, limit=10, refresh=False, only={"IC-2"})] == ["IC-2"]
    assert B.plan(wh, limit=10, refresh=False, only=set()) == []


def test_only_with_stated_sqft_judges_just_the_facilities_with_a_stated_size(tmp_path, monkeypatch):
    from pipeline.enrich import footprint
    db = tmp_path / "w.sqlite"
    w = warehouse.SqliteWarehouse(db); w.init_schema()
    with w.transaction() as c:
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, lat_lon, sq_ft) VALUES "
                  "('IC-1','r1','P',?, '120000'), ('IC-2','r1','Q','36.0,-81.0', NULL)", (POINT,))
    w.close()
    seen = []
    monkeypatch.setattr(footprint, "around", lambda pts, **kw: seen.extend(p["facility_id"] for p in pts) or [])
    assert B.main(["--db", str(db), "--dry-run", "--only-with-stated-sqft"]) == 0
    assert seen == ["IC-1"]


def test_a_small_containing_building_is_proposed_not_attached():
    # The office the geocode lands on, beside the plant: propose the plant, count nothing.
    d = B.decide(POINT, [bld("office", 0, 7573, True), bld("plant", 18, 78314)], {})
    assert d["outcome"] == "small_building"
    assert d["rows"]["plant"]["status"] == "proposed" and d["rows"]["office"]["status"] == "candidate"
    assert "7,573 sqft" in d["reason"] and "78,314" in d["reason"]
    # Nothing bigger within 30m: the small building itself is proposed.
    d = B.decide(POINT, [bld("shop", 0, 5084, True), bld("far", 80, 90000)], {})
    assert d["outcome"] == "small_building" and d["rows"]["shop"]["status"] == "proposed"
    assert B.confirmed_sqft(d["rows"], {"shop": 5084, "far": 90000}) == (None, [])
    # At the threshold it is a plant.
    assert B.decide(POINT, [bld("plant", 0, 10_000, True)], {})["outcome"] == "contains_point"


def test_writes_flush_in_batches_and_a_batch_boundary_loses_nothing(wh, monkeypatch):
    monkeypatch.setattr(B, "WRITE_BATCH", 2)
    with wh.transaction() as c:
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, lat_lon) VALUES ('IC-4','r1','R','37.0,-82.0')")
    m = [measured("IC-1", POINT, [bld("plant", 0, 90000, True)]),
         measured("IC-2", "36.0,-81.0", [bld("shed", 20, 2000)]),
         measured("IC-4", "37.0,-82.0", [bld("mill", 0, 40000, True), bld("plant", 50, 90000)])]
    rep = B.apply(wh, m, dry_run=False)
    assert rep["judged"] == 3 and rep["sqft_asserted"] == 2
    assert {r["facility_key"] for r in wh.query("SELECT facility_key FROM facility_building_review")} == {"IC-1", "IC-2", "IC-4"}
    # one footprint row per building even when two facilities see it
    assert wh.query("SELECT count(*) AS n FROM building_footprint WHERE building_id = 'plant'")[0]["n"] == 1
    assert len(wh.query("SELECT 1 FROM fact_assertions WHERE field_key = 'building_sqft'")) == 2
