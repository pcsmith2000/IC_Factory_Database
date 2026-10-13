"""A ruling that takes a plant out must reach golden; pipeline.ruling_check says when one has not."""
import csv

from pipeline import golden_refresh as gr, operator_apply as O, ruling_check as R

NOW = "v1+reg.a+ids.00000000+ctl.x"
COLUMNS = ["facility_id", "field", "value", "retrieved_date", "note"]


def _row(fid, value, date, field="existence_flag"):
    return {"facility_id": fid, "field": field, "value": value, "retrieved_date": date, "note": ""}


def test_the_latest_existence_ruling_decides():
    rows = [_row("IC-1", "not_ic", "2026-09-27"), _row("IC-1", "review", "2026-10-02"),
            _row("IC-2", "review", "2026-09-27"), _row("IC-2", "closed", "2026-10-02"),
            _row("IC-3", "34.1,-114.6", "2026-10-02", field="lat_lon")]
    assert set(R.excluding_rulings(rows)) == {"IC-2"}


def test_pending_and_employee_overrides_are_not_failures():
    rulings = R.excluding_rulings([_row(f"IC-{i}", "not_ic", "2026-10-13") for i in (1, 2, 3)])
    golden = [{"facility_key": "IC-1", "name": "botest"},
              {"facility_key": "IC-2", "name": "queued"},
              {"facility_key": "IC-3", "name": "brought back", "existence_flag": "active",
               "existence_flag__source": "adl_employee_feedback"},
              {"facility_key": "IC-4", "name": "untouched"}]
    out = R.unapplied(golden, rulings, pending={"IC-2"})
    assert [u["facility_id"] for u in out] == ["IC-1"]


def test_suspects_are_test_names_and_foreign_countries_without_a_ruling():
    golden = [{"facility_key": "IC-1", "name": "XPRODTEST"}, {"facility_key": "IC-2", "name": "YPRODTEST 1"},
              {"facility_key": "IC-3", "name": "botest"}, {"facility_key": "IC-4", "name": "Latest Metalworks, Inc."},
              {"facility_key": "IC-5", "name": "Intertek Testing Labs"}, {"facility_key": "IC-6", "name": "Trussworks", "country_based": "Japan"},
              {"facility_key": "IC-7", "name": "Stark Truss", "country_based": "USA"},
              {"facility_key": "IC-8", "name": "Placeholder plant"}]
    rulings = R.excluding_rulings([_row("IC-3", "not_ic", "2026-10-13")])
    assert [s["facility_id"] for s in R.suspects(golden, rulings)] == ["IC-1", "IC-2", "IC-6", "IC-8"]


def test_against_the_warehouse(new_wh, tmp_path):
    wh = new_wh()
    with wh.transaction() as c:
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, 't', 't')", [("IC-00001",), ("IC-00002",)])
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, ?, 'fl_bcis', ?, ?, 'A', 'on_current_list', '2026-09-21')",
                      [("r1", NOW, "IC-00001", "name", "botest"), ("r2", NOW, "IC-00001", "state", "FL"),
                       ("r3", NOW, "IC-00002", "name", "Raftco"), ("r4", NOW, "IC-00002", "state", "FL")])
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, name__source) VALUES ('IC-00001', ?, 'x', 'x')", (NOW,))
    gr.refresh(wh, all_facilities=True)
    gr.refresh(wh)                                        # drain what the seeding queued
    p = tmp_path / "o.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(COLUMNS)
        w.writerow(["IC-00001", "existence_flag", "not_ic", "2026-10-13", "test record (ruled by owner)"])
    rep = R.check(wh, path=p)                             # written down but never applied: the 2026-10-13 case
    assert [u["facility_id"] for u in rep["unapplied"]] == ["IC-00001"] and rep["suspects"] == []
    O.apply(wh, path=p)                                   # queues IC-00001: pending, not a failure
    assert R.check(wh, path=p)["unapplied"] == []
    gr.refresh(wh)
    assert R.check(wh, path=p)["unapplied"] == []         # landed: out of golden
