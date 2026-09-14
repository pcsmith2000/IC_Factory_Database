import sqlite3
from pathlib import Path
from pipeline import reconcile, golden, warehouse
from pipeline.contract import COLUMNS, normalise
from pipeline.registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent

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


def test_golden_field_traces_back_to_the_contract_row(tmp_path: Path):
    wh = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
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


def test_reload_is_idempotent_and_second_release_appends_facts_and_replaces_golden(tmp_path: Path):
    wh = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
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


def test_bigquery_is_selected_but_not_silently_skipped(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BQ_DATASET", "proj.ic_factory")
    try:
        warehouse.open_warehouse({"warehouse": {"engine": "sqlite"}}, tmp_path)
    except warehouse.WarehouseNotImplemented as e:
        assert "BigQuery" in str(e)
    else:
        raise AssertionError("BQ_DATASET must select the BigQuery engine and halt until it exists")
    monkeypatch.delenv("BQ_DATASET")
    wh = warehouse.open_warehouse({"warehouse": {"engine": "sqlite", "sqlite_path": "x/w.sqlite"}}, tmp_path)
    assert wh.path == tmp_path / "x" / "w.sqlite"
