"""Monitor fixes: evidenced corrections from the warehouse monitor, below people, above rosters."""
import csv

import pytest

from pipeline import golden, golden_refresh as gr, monitor_fix as M, warehouse

NOW = "v1+reg.a+ids.00000000+ctl.x"
ISSUE = "https://github.com/pcsmith2000/IC_Factory_Database/issues/57"


def _write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(M.COLUMNS); w.writerows(rows)
    return path


@pytest.fixture
def wh(tmp_path):
    w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    with w.transaction() as c:
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, 't', 't')", [("IC-00001",), ("IC-00002",)])
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, ?, 'ic_directories_more', ?, ?, 'A', 'on_current_list', '2026-09-21')",
                      [("r1", NOW, "IC-00001", "name", "Boxabl, Inc."), ("r2", NOW, "IC-00001", "state", "NJ"),
                       ("r3", NOW, "IC-00001", "zip", "89115"), ("r4", NOW, "IC-00002", "name", "Aura Prefab"),
                       ("r5", NOW, "IC-00002", "state", "VT")])
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, name__source) VALUES ('IC-00001', ?, 'x', 'x')", (NOW,))
    gr.refresh(w, all_facilities=True)
    yield w
    w.close()


def test_problems_refuse_what_a_monitor_may_not_decide(tmp_path):
    p = _write(tmp_path / "m.csv", [
        ["IC-00001", "existence_flag", "not_ic", "2026-09-26", ISSUE, "it looks like a shed maker"],
        ["IC-00001", "lat_lon", "1,2", "2026-09-26", ISSUE, "moved the pin somewhere"],
        ["IC-00001", "state", "Nevada", "2026-09-26", ISSUE, "ZIP 89115 is Nevada"],
        ["IC-00001", "zip", "891150000", "2026-09-26", ISSUE, "nine digits no dash"],
        ["IC-00001", "state", "NV", "2026-09-26", "", "ZIP 89115 is Nevada"],
        ["IC-00001", "state", "NV", "2026-09-26", "#57", "zip"],
        ["X-1", "state", "NV", "26/09/2026", "#57", "ZIP 89115 is Nevada"],
    ])
    msgs = M.problems(p)
    assert len(msgs) == 8
    assert not M.problems(_write(tmp_path / "ok.csv", [["IC-00001", "state", "NV", "2026-09-26", "#57", "ZIP 89115 is Nevada"]]))


def test_apply_corrects_golden_and_is_idempotent(wh, tmp_path):
    p = _write(tmp_path / "m.csv", [["IC-00001", "state", "NV", "2026-09-26", ISSUE, "ZIP 89115 and North Las Vegas are NV"],
                                    ["IC-99999", "state", "TX", "2026-09-26", ISSUE, "not a registered facility"]])
    out = M.apply(wh, path=p)
    assert out["written"] == 1 and out["skipped"][0]["facility_id"] == "IC-99999"
    assert M.apply(wh, path=p)["written"] == 0            # one assertion per facility, field, value, date
    gr.refresh(wh)                                        # the fact_assertions trigger queued IC-00001
    row = wh.query("SELECT state, state__source FROM golden_facility WHERE facility_key = 'IC-00001'")[0]
    assert (row["state"], row["state__source"]) == ("NV", "monitor_fix")
    other = wh.query("SELECT state FROM golden_facility WHERE facility_key = 'IC-00002'")[0]
    assert other["state"] == "VT"


def test_rank_only_people_beat_a_monitor_fix():
    rules = {"default_order": [], "fields": {"state": {"order": ["adl_employee_feedback", "operator", "site_visit", "class:A", "class:B"]}}}
    base = {"facility_id": "IC-1", "field": "state", "row_hash": "", "site_visit": False, "confidence": 1.0}
    roster = {**base, "value": "NJ", "source_id": "src", "source_class": "A", "basis": "on_current_list", "retrieved_date": "2026-09-30"}
    fix = {**base, "value": "NV", "source_id": "monitor_fix", "source_class": "monitor_fix", "basis": "monitor_fix", "retrieved_date": "2026-09-26"}
    person = {**base, "value": "CA", "source_id": "operator", "source_class": "operator", "basis": "operator", "retrieved_date": "2026-09-01"}
    web = {**base, "value": "AZ", "source_id": "web_research", "source_class": "web_research", "basis": "web_verified",
           "confidence": 0.8, "retrieved_date": "2026-09-01"}
    visit = {**roster, "value": "NM", "site_visit": True}
    assert golden.build_golden([roster, fix], rules)[0][0]["state"] == "NV"
    assert golden.build_golden([roster, visit, fix, web], rules)[0][0]["state"] == "NV"
    assert golden.build_golden([roster, fix, web, person], rules)[0][0]["state"] == "CA"


def test_the_checked_in_file_is_valid():
    assert M.problems() == []
