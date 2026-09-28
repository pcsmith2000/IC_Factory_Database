"""Operator assertions reach the warehouse: a person's ruling takes a plant out of golden between full runs."""
import csv

import pytest

from pipeline import golden_refresh as gr, operator_apply as O, warehouse

NOW = "v1+reg.a+ids.00000000+ctl.x"
COLUMNS = ["facility_id", "field", "value", "retrieved_date", "note"]


def _write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(COLUMNS); w.writerows(rows)
    return path


@pytest.fixture
def wh(tmp_path):
    w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    with w.transaction() as c:
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, 't', 't')", [("IC-00001",), ("IC-00002",), ("IC-00003",)])
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, ?, 'ic_directories_more', ?, ?, 'A', 'on_current_list', '2026-09-21')",
                      [("r1", NOW, "IC-00001", "name", "Walnut Grove Packaging"), ("r2", NOW, "IC-00001", "state", "IL"),
                       ("r3", NOW, "IC-00002", "name", "Raftco"), ("r4", NOW, "IC-00002", "state", "FL"),
                       ("r5", NOW, "IC-00003", "name", "Tri State Truss"), ("r6", NOW, "IC-00003", "state", "AZ")])
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, name__source) VALUES ('IC-00001', ?, 'x', 'x')", (NOW,))
    gr.refresh(w, all_facilities=True)
    yield w
    w.close()


def test_rulings_reach_golden_and_apply_is_idempotent(wh, tmp_path):
    assert len(wh.query("SELECT 1 FROM golden_facility")) == 3
    p = _write(tmp_path / "o.csv", [
        ["IC-00001", "existence_flag", "not_ic", "2026-09-27", "pallet plant (ruled by owner)"],
        ["IC-00002", "existence_flag", "closed", "2026-09-27", "dissolved in 2002 (ruled by owner)"],
        ["IC-00003", "lat_lon", "34.841038,-114.600772", "2026-09-24", "pin confirmed (ADL review)"],
        ["IC-99999", "existence_flag", "not_ic", "2026-09-27", "not a registered facility"],
    ])
    out = O.apply(wh, path=p)
    assert out["written"] == 3 and [s["facility_id"] for s in out["skipped"]] == ["IC-99999"]
    assert O.apply(wh, path=p)["written"] == 0            # one assertion per facility, field, value, date
    gr.refresh(wh)                                        # the fact_assertions trigger queued all three
    keys = {r["facility_key"] for r in wh.query("SELECT facility_key FROM golden_facility")}
    assert keys == {"IC-00003"}                           # not_ic and closed are out of golden
    row = wh.query("SELECT lat_lon, lat_lon__source FROM golden_facility WHERE facility_key = 'IC-00003'")[0]
    assert row["lat_lon__source"] == "operator"


def test_dry_run_writes_nothing(wh, tmp_path):
    p = _write(tmp_path / "o.csv", [["IC-00001", "existence_flag", "not_ic", "2026-09-27", "pallet plant"]])
    assert O.apply(wh, path=p, dry_run=True)["written"] == 1
    assert O.apply(wh, path=p)["written"] == 1


def test_operator_class_is_carried_across_releases():
    assert "operator" in gr.CARRIED_CLASSES
