import sqlite3, tempfile
from pathlib import Path
from pipeline import golden, warehouse
from pipeline.registry import load_yaml


def test_an_existing_golden_table_gains_the_new_columns_in_place():
    """CREATE TABLE IF NOT EXISTS is a no-op on a table that exists, so a warehouse laid down before
    website/sq_ft/operating_status would reject every load. The migration adds them; a table that
    has them is left alone."""
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "w.sqlite"
        old_fields = [f for f in warehouse.GOLDEN_FIELDS if f not in ("website", "sq_ft", "operating_status")]
        cols = ", ".join(f"{f} TEXT, {f}__source TEXT" for f in old_fields)
        con = sqlite3.connect(db)
        con.execute(f"CREATE TABLE golden_facility (facility_key TEXT PRIMARY KEY, release_tag TEXT NOT NULL, {cols}, n_assertions INTEGER, n_sources INTEGER)")
        con.commit(); con.close()
        before = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(golden_facility)")}
        assert "website" not in before
        wh = warehouse.SqliteWarehouse(db)                       # init_schema -> _migrate_golden
        after = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(golden_facility)")}
        for f in ("website", "sq_ft", "operating_status"):
            assert f in after and f"{f}__source" in after
        assert before <= after                                   # nothing dropped
        wh.close()
        wh2 = warehouse.SqliteWarehouse(db)                      # idempotent: second open adds nothing
        with wh2.transaction() as c:
            assert wh2._migrate_golden(c) == []
        wh2.close()


def test_a_website_on_a_row_becomes_a_golden_field_under_the_rules():
    rows = [{"facility_id": "IC-1", "source_id": "ga_dca", "retrieved_date": "2026-09-17", "row_hash": "h1",
             "status_basis": "on_current_list", "name_verbatim": "Panel Built", "website": "www.panel-built.com",
             "sq_ft": "", "operating_status": ""},
            {"facility_id": "IC-1", "source_id": "lead_addresses", "retrieved_date": "2026-09-17", "row_hash": "h2",
             "status_basis": "none", "name_verbatim": "Panel Built, Inc.", "website": "https://www.panelbuilt.com",
             "sq_ft": "25000", "operating_status": ""}]
    a = golden.assertions_from_rows(rows, {"ga_dca": "A", "lead_addresses": "D"})
    assert {x["field"] for x in a} >= {"name", "website", "sq_ft"}
    assert not any(x["field"] == "operating_status" for x in a)          # blank is not asserted
    rules = load_yaml(Path("registry/survivorship.yaml"))
    g, conflicts = golden.build_golden(a, rules)
    row = g[0]
    assert row["website"] == "www.panel-built.com" and row["website__source"] == "ga_dca"   # class A over class D
    assert row["sq_ft"] == "25000" and row["sq_ft__source"] == "lead_addresses"
    assert any(c["field"] == "website" for c in conflicts)               # two values, one winner, recorded
