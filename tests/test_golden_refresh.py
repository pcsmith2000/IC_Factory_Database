"""Continuous golden (#44): a queue filled by a trigger, drained per facility, equal to a full rebuild."""
import os

import pytest

from pipeline import facility_registry as fr
from pipeline import golden_refresh as gr
from pipeline import warehouse

PG_URL = os.environ.get("TEST_DATABASE_URL")
NOW = "v1+reg.a+ids.00000000+ctl.x"       # current release, loaded through the permanent registry
OLD = "v1+reg.b+ids.aaaaaaaa+ctl.x"       # an old release from a diverged registry
TABLES = ("golden_dirty", "facility_event", "facility_match_key", "legacy_id_map", "release_registry",
          "golden_facility", "fact_assertions", "facility")


@pytest.fixture(params=["sqlite", "postgres"])
def wh(request, tmp_path):
    if request.param == "sqlite":
        w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    else:
        if not PG_URL:
            pytest.skip("TEST_DATABASE_URL not set")
        w = warehouse.PostgresWarehouse(PG_URL)
        with w.transaction() as c:
            for t in TABLES:
                c.execute(f"DELETE FROM {t}")
    _world(w)
    yield w
    w.close()


_n = [0]


def fact(wh, fid, field, value, tag=NOW, source="tx_tdlr", cls="A", basis="on_current_list", date="2026-09-01"):
    _n[0] += 1
    with wh.transaction() as c:
        c.execute("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                  "source_class, basis, date_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (f"a{_n[0]}", tag, fid, source, field, value, cls, basis, date))


def _world(wh):
    with wh.transaction() as c:
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, 't', 't')", [("IC-00001",), ("IC-00002",)])
        # The old release called Ladabuild IC-00009; the crosswalk says it is IC-00001 now.
        c.execute("INSERT INTO release_registry VALUES (?, 'aaaaaaaa')", (OLD,))
        c.execute("INSERT INTO legacy_id_map VALUES ('aaaaaaaa', 'IC-00009', 'IC-00001', 'identity', 0.9)")
        # Golden as the last full run left it, plus a facility an employee created in ADL_Viz.
        c.executemany("INSERT INTO golden_facility (facility_key, release_tag, name, name__source) VALUES (?, ?, ?, 'x')",
                      [("IC-00001", NOW, "Ladabuild"), ("IC-00002", NOW, "Blueprint Robotics"), ("ADL-req1", NOW, "Hand Added")])
    fact(wh, "IC-00001", "name", "Ladabuild")
    fact(wh, "IC-00001", "state", "CO")
    fact(wh, "IC-00002", "name", "Blueprint Robotics")
    fact(wh, "IC-00002", "state", "MD")
    # A rooftop geocoded under the old number, carried by the crosswalk, not by id.
    fact(wh, "IC-00009", "lat_lon", "39.0639,-108.5506", tag=OLD, source="geocode:geocodio", cls="enrichment", basis="rooftop")


def golden(wh):
    return {r["facility_key"]: {k: v for k, v in r.items() if k not in ("n_assertions", "n_sources")}
            for r in wh.query("SELECT * FROM golden_facility")}


def test_the_trigger_queues_every_new_fact(wh):
    queued = {(r["facility_key"], r["release_tag"]) for r in wh.query("SELECT * FROM golden_dirty")}
    assert ("IC-00009", OLD) in queued and ("IC-00001", NOW) in queued


def test_a_fact_under_an_old_number_reaches_its_permanent_plant(wh):
    rep = gr.refresh(wh)
    g = golden(wh)
    assert g["IC-00001"]["lat_lon"] == "39.0639,-108.5506"
    assert g["IC-00001"]["lat_lon__source"] == "geocode:geocodio"
    assert "IC-00009" not in g
    assert not wh.query("SELECT 1 FROM golden_dirty")               # drained
    assert rep["facilities"] == 2


def test_an_unregistered_facility_is_never_touched(wh):
    fact(wh, "ADL-req1", "phone", "555-0100")
    rep = gr.refresh(wh)
    assert golden(wh)["ADL-req1"]["name"] == "Hand Added"            # still there, unchanged
    assert rep["unresolved_pairs"] == 1
    gr.refresh(wh, all_facilities=True)
    assert "ADL-req1" in golden(wh)


def test_incremental_refreshes_equal_a_full_rebuild(wh):
    gr.refresh(wh)
    fact(wh, "IC-00002", "website", "blueprint.example", source="tako_ai_search", cls="tako_ai_search", tag=OLD)
    fact(wh, "IC-00001", "phone", "970-555-0101")
    gr.refresh(wh)
    incremental = golden(wh)
    gr.refresh(wh, all_facilities=True)
    assert golden(wh) == incremental
    assert incremental["IC-00001"]["phone"] == "970-555-0101"


def test_an_out_of_state_coordinate_is_withheld_and_is_not_a_loss(wh):
    gr.refresh(wh)
    # A later rooftop that lands in Maryland for a Colorado plant: E10 withholds it, and the
    # earlier Colorado rooftop still wins.
    fact(wh, "IC-00001", "lat_lon", "39.29,-76.61", source="geocode:geocodio", cls="enrichment",
         basis="rooftop", date="2026-09-20")
    rep = gr.refresh(wh)
    assert golden(wh)["IC-00001"]["lat_lon"] == "39.0639,-108.5506"
    assert rep["coordinates_withheld"] == 1 and not rep["held_for_loss"]


def test_a_facility_that_would_lose_a_field_is_held_in_the_queue(wh):
    gr.refresh(wh)
    with wh.transaction() as c:      # golden carries a field no assertion supports any more
        c.execute("UPDATE golden_facility SET phone = '555-9999', phone__source = 'gone' WHERE facility_key = 'IC-00002'")
    fact(wh, "IC-00002", "city", "Baltimore")
    rep = gr.refresh(wh)
    assert rep["held_for_loss"] == [{"facility_id": "IC-00002", "fields": ["phone"]}]
    assert golden(wh)["IC-00002"]["phone"] == "555-9999"             # not written
    assert wh.query("SELECT 1 FROM golden_dirty WHERE facility_key = 'IC-00002'")   # still queued


def test_a_merge_moves_the_facts_and_removes_the_old_row(wh):
    gr.refresh(wh)
    fr.merge(wh, "IC-00002", "IC-00001", actor="t", reason="same plant")
    rep = gr.refresh(wh)
    g = golden(wh)
    assert "IC-00002" not in g and rep["removed_not_live"] == 1
    assert g["IC-00001"]["name"] in ("Ladabuild", "Blueprint Robotics")    # survivorship picked one


def test_dry_run_writes_and_drains_nothing(wh):
    before = golden(wh)
    rep = gr.refresh(wh, dry_run=True)
    assert rep["written"] >= 1 and golden(wh) == before
    assert wh.query("SELECT 1 FROM golden_dirty")


def test_the_view_resolves_a_registry_era_release_directly(wh):
    rows = wh.query("SELECT facility_key, release_tag, permanent_facility_id, resolve_method FROM v_assertions_resolved")
    by = {(r["facility_key"], r["release_tag"]): r for r in rows}
    assert by[("IC-00001", NOW)]["permanent_facility_id"] == "IC-00001"
    assert by[("IC-00001", NOW)]["resolve_method"] == "direct"
    assert by[("IC-00009", OLD)]["permanent_facility_id"] == "IC-00001"
