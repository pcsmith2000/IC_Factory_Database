"""One IC-number, several plants: facts must reach a plant through the registry, never by number.

Reproduces the two "mis-merges" found in manual review on 2026-10-03:

  IC-96455  golden: Dwell Alabama, 1214 Meridian St N, Huntsville AL (256 phone).
            Registry 4ec733c1 used the same number for County Truss, 504 Station Rd, Easton ME
            (207 phone, countytruss.com from an Overture enrichment).
  IC-95934  golden: YOAN CORTERAS, 37 Hickory Corner Rd, Philippi WV (an OSHA row).
            Registry 4ec733c1 used it for BUILDERS FIRSTSOURCE SOUTHEAST GROUP, 252 Highway 308, AL;
            registry aad29e92 for Wisconsin Truss, Inc., 609 Industrial Park Rd, Cornell WI.

Nothing clustered these rows together: reconcile keys every signature by state, and the crosswalk
(pipeline/legacy_ids.py) resolves each (registry, number) to the right permanent plant. The mix is
what a reader sees when it selects fact_assertions WHERE facility_key = 'IC-96455' across releases.
These tests hold golden, and the enrichment readers that used to key on the raw number, to the
registry's answer.
"""
import pytest

from pipeline import golden_refresh as gr
from pipeline import legacy_ids as L
from pipeline import warehouse
from pipeline.enrich import _db as enrich_db

NOW = "v1+reg.d0c18c1+ids.23d47f52+ctl.x"     # the current golden release
OLD_4E = "v1+reg.0d79d86+ids.4ec733c1+ctl.x"  # a release from a diverged registry
OLD_AA = "v1+reg.afffc3d+ids.aad29e92+ctl.x"  # another

_n = [0]


def fact(c, fid, tag, source, cls, field, value, basis="on_current_list"):
    _n[0] += 1
    c.execute("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
              "source_class, basis, date_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-09-20')",
              (f"x{_n[0]}", tag, fid, source, field, value, cls, basis))


def plant(c, fid, tag, source, cls, **fields):
    for field, value in fields.items():
        fact(c, fid, tag, source, cls, field, value)


@pytest.fixture
def wh(tmp_path):
    w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    with w.transaction() as c:
        current = {"IC-96455": "Dwell Alabama", "IC-96435": "County Truss", "IC-95934": "YOAN CORTERAS",
                   "IC-00743": "BUILDERS FIRSTSOURCE SOUTHEAST GROUP", "IC-96809": "Wisconsin Truss, Inc."}
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, 't', 't')", [(f,) for f in current])
        c.executemany("INSERT INTO golden_facility (facility_key, release_tag, name, name__source) VALUES (?, ?, ?, 'x')",
                      [(f, NOW, n) for f, n in current.items()])
        # The current release: five plants, each under its permanent number.
        plant(c, "IC-96455", NOW, "sbca_cm", "C", name="Dwell Alabama", address="1214 Meridian Street North",
              city="Huntsville", state="AL", zip="35801", phone="2564263740")
        plant(c, "IC-96435", NOW, "sbca_cm", "C", name="County Truss", address="504 Station Rd",
              city="EASTON", state="ME", zip="04740", phone="2074887740")
        plant(c, "IC-95934", NOW, "osha_inspections", "B", name="YOAN CORTERAS", address="37 HICKORY CORNER RD.",
              city="PHILIPPI", state="WV", zip="26416")
        plant(c, "IC-00743", NOW, "osha_inspections", "B", name="BUILDERS FIRSTSOURCE SOUTHEAST GROUP",
              address="252 HIGHWAY 308", city="SHELBY", state="AL", zip="35143")
        plant(c, "IC-96809", NOW, "sbca_cm", "C", name="Wisconsin Truss, Inc.", address="609 Industrial Park Rd",
              city="Cornell", state="WI", zip="54732", phone="7152396465")
        # Registry 4ec733c1 numbered County Truss IC-96455 and BFS Southeast IC-95934.
        plant(c, "IC-96455", OLD_4E, "sbca_cm", "C", name="County Truss", address="504 Station Rd",
              city="EASTON", state="ME", zip="04740", phone="2074887740")
        fact(c, "IC-96455", OLD_4E, "overture:place", "enrichment", "website", "https://www.countytruss.com/index.php",
             basis="place_match")
        fact(c, "IC-96455", OLD_4E, "geocode:geocodio", "enrichment", "lat_lon", "46.6384,-67.9126", basis="rooftop")
        plant(c, "IC-95934", OLD_4E, "osha_inspections", "B", name="BUILDERS FIRSTSOURCE SOUTHEAST GROUP",
              address="252 HIGHWAY 308", city="SHELBY", state="AL", zip="35143")
        # Registry aad29e92 numbered Wisconsin Truss IC-95934.
        plant(c, "IC-95934", OLD_AA, "sbca_cm", "C", name="Wisconsin Truss, Inc.", address="609 Industrial Park Rd",
              city="Cornell", state="WI", zip="54732", phone="7152396465")
        fact(c, "IC-95934", OLD_AA, "overture:place", "enrichment", "website", "http://www.wisconsintruss.com",
             basis="place_match")
    L.crosswalk(w)
    yield w
    w.close()


def golden(wh):
    return {r["facility_key"]: r for r in wh.query("SELECT * FROM golden_facility")}


def test_the_raw_number_really_does_name_three_plants(wh):
    # The symptom the review saw: by facility_key alone, IC-95934 is in WV, AL and WI at once.
    states = {r["value"] for r in wh.query(
        "SELECT value FROM fact_assertions WHERE facility_key = 'IC-95934' AND field_key = 'state'")}
    assert states == {"WV", "AL", "WI"}


def test_the_crosswalk_sends_each_old_number_to_its_own_plant(wh):
    m = {(r["registry_hash"], r["legacy_id"]): r["facility_id"] for r in wh.query("SELECT * FROM legacy_id_map")}
    assert m[("23d47f52", "IC-96455")] == "IC-96455"
    assert m[("4ec733c1", "IC-96455")] == "IC-96435"        # County Truss, not Dwell Alabama
    assert m[("4ec733c1", "IC-95934")] == "IC-00743"        # BFS Southeast
    assert m[("aad29e92", "IC-95934")] == "IC-96809"        # Wisconsin Truss
    assert m[("23d47f52", "IC-95934")] == "IC-95934"


def test_golden_does_not_mix_dwell_alabama_with_county_truss(wh):
    gr.refresh(wh, all_facilities=True)
    g = golden(wh)
    dwell, county = g["IC-96455"], g["IC-96435"]
    assert (dwell["name"], dwell["state"], dwell["phone"]) == ("Dwell Alabama", "AL", "2564263740")
    assert dwell["website"] is None and dwell["lat_lon"] is None
    assert (county["name"], county["state"], county["phone"]) == ("County Truss", "ME", "2074887740")
    assert county["website"] == "https://www.countytruss.com/index.php"
    assert county["lat_lon"] == "46.6384,-67.9126"


def test_golden_does_not_mix_yoan_corteras_bfs_and_wisconsin_truss(wh):
    gr.refresh(wh, all_facilities=True)
    g = golden(wh)
    yoan = g["IC-95934"]
    assert (yoan["name"], yoan["address"], yoan["state"]) == ("YOAN CORTERAS", "37 HICKORY CORNER RD.", "WV")
    assert yoan["website"] is None and yoan["phone"] is None
    assert g["IC-96809"]["website"] == "http://www.wisconsintruss.com"
    assert g["IC-00743"]["state"] == "AL"


def test_enrichment_reads_rooftops_through_the_registry(wh):
    # County Truss's rooftop was geocoded under IC-96455 in registry 4ec733c1. Read by the raw number
    # it made Dwell Alabama look done (never geocoded) and County Truss look undone (geocoded twice).
    rows = {r["facility_key"]: r for r in wh.query(enrich_db.SELECT_GOLDEN)}
    assert rows["IC-96455"]["has_rooftop"] in (False, 0)
    assert rows["IC-96435"]["has_rooftop"] in (True, 1)
    assert all(r["geocode_tried"] in (False, 0) for r in rows.values())


def test_no_reader_joins_golden_to_facts_by_the_raw_number():
    # A lint for the pattern itself: golden's facility_key equated with fact_assertions.facility_key,
    # with nothing pinning the release. The crossmatch donor queries and the Overture control query
    # used it; they now read v_assertions_resolved.permanent_facility_id.
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "pipeline"
    raw = re.compile(r"JOIN\s+fact_assertions\s+a\s+ON\s+a\.facility_key\s*=\s*g\.facility_key(?![^\"']*release_tag)", re.I)
    offenders = [str(p.relative_to(root)) for p in sorted((root / "recovery").glob("*.py")) + [root / "enrich" / "_db.py"]
                 if raw.search(p.read_text())]
    assert offenders == []
