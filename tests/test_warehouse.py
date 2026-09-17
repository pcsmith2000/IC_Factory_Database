import os
from pathlib import Path
import pytest
from pipeline import reconcile, golden, warehouse
from pipeline.contract import COLUMNS, normalise
from pipeline.registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("TEST_DATABASE_URL")   # e.g. postgresql://ic:ic@127.0.0.1/ic_factory — postgres tests skip without it


@pytest.fixture(params=["sqlite", "postgres"])
def wh(request, tmp_path):
    if request.param == "sqlite":
        w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    else:
        if not PG_URL:
            pytest.skip("TEST_DATABASE_URL not set")
        w = warehouse.PostgresWarehouse(PG_URL)
        with w.transaction() as c:   # each test starts from an empty warehouse
            for t in ("fact_assertions", "dim_facility", "dim_source", "dim_field", "dim_date", "golden_facility", "conflicts",
                      "fact_release_metrics", "ref_control", "ref_source_registry", "ref_known_gaps", "ref_source_row"):
                c.execute(f"DELETE FROM {t}")
    yield w
    w.close()

def _row(sid, name, addr, city, st, pos="1", **kw):
    r = {c: "" for c in COLUMNS}
    r.update(source_id=sid, source_url=f"https://{sid}.example/list", source_document=f"{sid}.pdf", retrieved_date="2026-09-14",
             row_position=pos, status_basis="on_current_list", name_verbatim=name, address_verbatim=addr, city_verbatim=city, state_verbatim=st)
    r.update(kw); return r

ROWS = [
    _row("pa_dced", "Legacy Building Solutions", "19500 County Rd 142", "Maine Prairie", "MN", "7"),
    _row("iibc", "LEGACY BUILDING SOLUTIONS INC", "19500 County Road 142", "Saint Augusta", "MN", "12"),
    _row("tx_tdlr", "Aura Prefab, LLC", "5730 Clinton Dr", "Houston", "TX", "3", expiry_date="2027-01-31", status_basis="dated_expiry"),
]
SRC_CLASS = {"pa_dced": "A", "iibc": "C", "tx_tdlr": "A"}
REGISTRY = {"sources": [{"id": s, "class": c, "method": "download", "status": "active"} for s, c in SRC_CLASS.items()]}


def _build(tmp_path, tag):
    rec = reconcile.run(normalise(ROWS), tmp_path / "ids.json")
    rules = load_yaml(ROOT / "registry" / "survivorship.yaml")
    asserts = golden.assertions_from_rows(rec["rows"], SRC_CLASS)
    gold, conflicts = golden.build_golden(asserts, rules)
    record = {"pipeline_version": "test", "started": "2026-09-14T00:00:00+00:00", "registry_version": "abc", "registry_file_sha": "x",
              "gates": [{"gate": "G1 dedupe audit", "passed": True, "summary": "", "details": {"rate": 0.0}}],
              "layers": {"7_measure": {"recall": {"recall": 0.96}}}, "release": {"tag": tag, "published_count": len(gold), "raw_count": len(gold)}}
    return rec, rules, asserts, gold, conflicts, record


def _load(wh, tmp_path, tag):
    rec, rules, asserts, gold, conflicts, record = _build(tmp_path, tag)
    return wh.load_release(record, assertions=asserts, golden=gold, conflicts=conflicts, facilities=rec["facilities"], rows=rec["rows"],
                           registry=REGISTRY, registry_text="version: 1", rules=rules, control_rows=[], control_sha=None,
                           known_gaps={"states": {"OH": "no roster"}}, survivorship_hash="s"), asserts, gold


def test_golden_field_traces_back_to_the_contract_row(wh, tmp_path: Path):
    summary, asserts, gold = _load(wh, tmp_path, "v-test+1")
    assert summary["assertions_appended"] == len(asserts) and summary["golden_rows"] == len(gold) == 2
    legacy = next(g for g in gold if g["state"] == "MN")
    # name: class A (pa_dced) outranks class C (iibc) — the golden name is the PA spelling
    assert legacy["name"] == "Legacy Building Solutions" and legacy["name__source"] == "pa_dced"
    prov = wh.provenance(legacy["facility_id"], "name")
    assert len(prov) == 1
    p = prov[0]
    assert p["source_key"] == "pa_dced" and p["source_url"] == "https://pa_dced.example/list"
    assert p["source_document"] == "pa_dced.pdf" and p["row_position"] == "7" and p["row_hash"]
    # the conflict between the two spellings is recorded, not resolved by deletion
    assert wh.query("SELECT count(*) AS n FROM conflicts WHERE field_key='name'")[0]["n"] == 1
    assert wh.query("SELECT count(*) AS n FROM fact_assertions WHERE field_key='name' AND facility_key=?".replace("?", f"'{legacy['facility_id']}'"))[0]["n"] == 2
    # operator/lookup sources exist so a correction can be joined
    assert {r["source_key"] for r in wh.query("SELECT source_key FROM dim_source")} >= {"pa_dced", "iibc", "tx_tdlr", "operator", "lookup"}


def test_reload_is_idempotent_and_second_release_appends_facts_and_replaces_golden(wh, tmp_path: Path):
    a, asserts, _ = _load(wh, tmp_path, "v-test+1")
    b, _, _ = _load(wh, tmp_path, "v-test+1")
    assert b["assertions_appended"] == 0 and b["assertions_total"] == len(asserts)
    c, _, _ = _load(wh, tmp_path, "v-test+2")
    assert c["assertions_appended"] == len(asserts) and c["assertions_total"] == 2 * len(asserts)
    assert wh.query("SELECT DISTINCT release_tag FROM golden_facility") == [{"release_tag": "v-test+2"}]
    fac = wh.query("SELECT first_seen_release, last_seen_release FROM dim_facility")
    assert all(f["first_seen_release"] == "v-test+1" and f["last_seen_release"] == "v-test+2" for f in fac)
    assert [r["release_tag"] for r in wh.query("SELECT release_tag FROM fact_release_metrics ORDER BY release_tag")] == ["v-test+1", "v-test+2"]
    assert wh.query("SELECT state, cause FROM ref_known_gaps WHERE release_tag='v-test+2'") == [{"state": "OH", "cause": "no roster"}]


def test_database_url_selects_postgres_and_is_never_silently_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False); monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)
    w = warehouse.open_warehouse({"warehouse": {"engine": "sqlite", "sqlite_path": "x/w.sqlite"}}, tmp_path)
    assert w.engine == "sqlite" and w.path == tmp_path / "x" / "w.sqlite"; w.close()
    monkeypatch.setenv("IC_WAREHOUSE_ENGINE", "postgres")
    with pytest.raises(warehouse.WarehouseNotImplemented, match="DATABASE_URL"):
        warehouse.open_warehouse({"warehouse": {"engine": "sqlite"}}, tmp_path)
    monkeypatch.delenv("IC_WAREHOUSE_ENGINE")
    if PG_URL:
        monkeypatch.setenv("DATABASE_URL", PG_URL)
        w = warehouse.open_warehouse({"warehouse": {"engine": "sqlite"}}, tmp_path)
        assert w.engine == "postgres" and "***" in w.path and ":ic@" not in w.path; w.close()


def test_adding_a_golden_field_widens_a_database_that_predates_it(tmp_path: Path):
    """CREATE TABLE IF NOT EXISTS does not widen an existing table. Without the reconcile step in
    init_schema, a warehouse built before `building_sqft` joined GOLDEN_FIELDS would keep its old
    shape and silently drop every write of that field — and CREATE VIEW v_golden_field, which names
    every golden column, would fail outright."""
    older = [f for f in warehouse.GOLDEN_FIELDS if f not in ("building_sqft", "existence_flag")]
    assert len(older) < len(warehouse.GOLDEN_FIELDS), "this test needs a field newer than the rest"

    import sqlite3
    cols = ", ".join(f'{f} TEXT, "{f}__source" TEXT' for f in older)
    con = sqlite3.connect(tmp_path / "old.sqlite")
    con.execute(f"CREATE TABLE golden_facility (facility_key TEXT PRIMARY KEY, release_tag TEXT NOT NULL, "
                f"{cols}, n_assertions INTEGER, n_sources INTEGER)")
    con.commit(); con.close()

    w = warehouse.SqliteWarehouse(tmp_path / "old.sqlite")
    have = w.existing_columns("golden_facility")
    for f in warehouse.GOLDEN_FIELDS:
        assert f in have and f"{f}__source" in have, f"{f} was not added to a pre-existing golden_facility"
    w.query("SELECT COUNT(*) AS n FROM v_golden_field")     # the view builds against the widened table
    w.close()


def test_every_enrichment_source_has_a_dim_source_row():
    """Enrichment assertions carry source ids that are in no registry. Without a dim_source row,
    v_provenance answers 'who says so' with a null join."""
    from pipeline.enrich import _db
    emitted = {"enrich:locate", "geocode:geocodio", "overture:building", "enrich:existence"}
    assert emitted <= set(warehouse.SYNTHETIC_SOURCES), emitted - set(warehouse.SYNTHETIC_SOURCES)
    assert all(warehouse.SYNTHETIC_SOURCES[s]["class"] == _db.assertion("f", "x", "v", source_id=s)["source_class"]
               for s in emitted), "dim_source class must match the class the assertions carry"


def test_promote_rebuilds_exactly_the_golden_the_loader_wrote(wh, tmp_path: Path):
    """Stage 13 replaces golden_facility from what the database holds, so a round trip through
    fact_assertions must reproduce what the loader computed in memory. Anything the write-then-read
    loses — the 0/1 integer for site_visit, a null date_key, source_class — would show up here as a
    different winner, and on main it would show up as a silently rewritten release."""
    from pipeline.enrich import promote
    _, asserts, gold = _load(wh, tmp_path, "v-promote")

    read_back = wh.query(
        "SELECT facility_key AS facility_id, source_key AS source_id, source_class, "
        "       date_key AS retrieved_date, row_hash, basis, site_visit, confidence, "
        "       field_key AS field, value "
        "FROM fact_assertions WHERE release_tag = ?", ("v-promote",))
    assert read_back, "the loader wrote no assertions to read back"

    rules = load_yaml(ROOT / "registry" / "survivorship.yaml")
    rebuilt, _ = promote.build(read_back, rules)

    def shape(rows):
        return {r["facility_id"]: {k: v for k, v in r.items()
                                   if k.endswith("__source") or k in warehouse.GOLDEN_FIELDS}
                for r in rows}
    assert shape(rebuilt) == shape(gold)


def test_asserted_at_is_added_to_a_fact_table_that_predates_it(tmp_path: Path):
    """Same reconcile as the golden columns, for the same reason: without the column the loader's
    INSERT fails outright, and without the value survivorship cannot order two same-day
    measurements of the same facility."""
    import sqlite3
    con = sqlite3.connect(tmp_path / "old.sqlite")
    con.execute("""CREATE TABLE fact_assertions (
        assertion_id TEXT NOT NULL, release_tag TEXT NOT NULL,
        facility_key TEXT NOT NULL, source_key TEXT NOT NULL, field_key TEXT NOT NULL, date_key TEXT,
        value TEXT, basis TEXT, site_visit INTEGER, row_hash TEXT, confidence REAL, source_class TEXT,
        PRIMARY KEY (assertion_id, release_tag))""")
    con.commit(); con.close()

    w = warehouse.SqliteWarehouse(tmp_path / "old.sqlite")
    assert "asserted_at" in w.existing_columns("fact_assertions")
    w.close()


def test_the_loader_stamps_asserted_at_so_a_reload_can_be_ordered(wh, tmp_path: Path):
    """Every assertion the loader writes carries when it was written, so a later run's assertion
    sorts after an earlier one even when both claim the same retrieved_date."""
    _load(wh, tmp_path, "v-stamp")
    rows = wh.query("SELECT asserted_at FROM fact_assertions WHERE release_tag = ?", ("v-stamp",))
    assert rows and all(r["asserted_at"] for r in rows), "the loader left asserted_at empty"
