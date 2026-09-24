"""reconcile resolves and mints through the warehouse registry (#43, epic #39)."""
import json
import os
from pathlib import Path

import pytest

from pipeline import facility_registry as fr
from pipeline import gates, reconcile, warehouse
from pipeline.contract import COLUMNS, normalise

PG_URL = os.environ.get("TEST_DATABASE_URL")
REGISTRY_TABLES = ("facility_event", "facility_match_key", "legacy_id_map", "facility")


@pytest.fixture(params=["sqlite", "postgres"])
def wh(request, tmp_path):
    if request.param == "sqlite":
        w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    else:
        if not PG_URL:
            pytest.skip("TEST_DATABASE_URL not set")
        w = warehouse.PostgresWarehouse(PG_URL)
        with w.transaction() as c:
            for t in REGISTRY_TABLES + ("golden_facility",):
                c.execute(f"DELETE FROM {t}")
            c.execute(f"SELECT setval('facility_id_seq', {fr.ID_FLOOR}, false)")
    yield w
    w.close()


def _row(sid, name, addr, city, st, **kw):
    r = {c: "" for c in COLUMNS}
    r.update(source_id=sid, source_url="u", source_document="d", retrieved_date="2026-09-14", row_position="1",
             status_basis="on_current_list", name_verbatim=name, address_verbatim=addr, city_verbatim=city, state_verbatim=st)
    r.update(kw)
    return r


ROWS = [
    _row("pa", "Legacy Building Solutions", "19500 County Rd 142", "Maine Prairie", "MN"),
    _row("iibc", "LEGACY BUILDING SOLUTIONS INC", "19500 County Road 142", "Saint Augusta", "MN"),
    _row("tx", "Aura Prefab, LLC", "5730 Clinton Dr", "Houston", "TX"),
]


def _seeded(wh, tmp_path):
    """Seed the warehouse registry from a file-registry run, the way production was seeded (#41)."""
    reg = tmp_path / "seed_ids.json"
    first = reconcile.run(normalise(ROWS), reg)
    with wh.transaction() as c:
        c.executemany("INSERT INTO golden_facility (facility_key, release_tag) VALUES (?, 'r1')",
                      [(f["facility_id"],) for f in first["facilities"]])
    fr.seed(wh, reg)
    return {f["name"]: f["facility_id"] for f in first["facilities"]}


def _db(wh, tmp_path):
    return fr.DbIdRegistry(wh, actor="test", export_path=tmp_path / "id_registry.json")


def test_a_rerun_through_the_warehouse_issues_nothing(wh, tmp_path):
    ids = _seeded(wh, tmp_path)
    rec = reconcile.run(normalise(ROWS), tmp_path / "unused.json", registry=_db(wh, tmp_path))
    assert rec["ids_issued"] == 0 and rec["ids_attached"] == 0
    assert {f["name"]: f["facility_id"] for f in rec["facilities"]} == ids
    assert gates.g3_id_stability(rec["ids_issued"], 0, is_rerun=True).passed


def test_a_plant_that_gains_an_address_keeps_its_number(wh, tmp_path):
    # A plant first known by name and city only...
    rows = [_row("x", "Falcon Structures", "", "Manor", "TX")]
    reg = tmp_path / "seed_ids.json"
    first = reconcile.run(normalise(rows), reg)
    fid = first["facilities"][0]["facility_id"]
    with wh.transaction() as c:
        c.execute("INSERT INTO golden_facility (facility_key, release_tag) VALUES (?, 'r1')", (fid,))
    fr.seed(wh, reg)
    # ...is later listed with a street address by a second source. The cluster's signature is now
    # the street key, which the registry has never seen; the name+city row folded into it has.
    later = rows + [_row("y", "FALCON STRUCTURES", "9900 Hwy 290 E", "Manor", "TX")]
    rec = reconcile.run(normalise(later), tmp_path / "unused.json", registry=_db(wh, tmp_path))
    assert [f["facility_id"] for f in rec["facilities"]] == [fid]
    assert rec["ids_issued"] == 0 and rec["ids_attached"] == 1
    kinds = [r["kind"] for r in wh.query("SELECT kind FROM facility_event WHERE facility_id = ?", (fid,))]
    assert "attach" in kinds


def test_a_new_plant_draws_from_the_sequence_above_every_old_number(wh, tmp_path):
    _seeded(wh, tmp_path)
    rows = ROWS + [_row("ca", "Brand New Modular", "1 Ocean Ave", "Oakland", "CA")]
    rec = reconcile.run(normalise(rows), tmp_path / "unused.json", registry=_db(wh, tmp_path))
    new = [f for f in rec["facilities"] if f["name"] == "Brand New Modular"][0]["facility_id"]
    assert new == f"IC-{fr.ID_FLOOR:05d}" and rec["ids_issued"] == 1
    assert wh.query("SELECT status FROM facility WHERE facility_id = ?", (new,)) == [{"status": "active"}]
    # and the file is kept as a superset export, with next never behind the sequence
    exported = json.loads((tmp_path / "id_registry.json").read_text())
    assert exported["ids"]["CA|S|1 ocean ave"] == new and exported["next"] == fr.ID_FLOOR + 1


def test_two_plants_claiming_one_cluster_mints_rather_than_merges(wh, tmp_path):
    _seeded(wh, tmp_path)
    db = _db(wh, tmp_path)
    keys = sorted(db.keys)
    fid = db.get("ZZ|S|new street", alts=keys[:2])          # two known keys, two different plants
    assert fid.startswith("IC-") and db.ambiguous_this_run == 1 and db.issued_this_run == 1


def test_a_merged_number_resolves_to_its_root(wh, tmp_path):
    ids = _seeded(wh, tmp_path)
    a, b = ids["Legacy Building Solutions"], ids["Aura Prefab, LLC"]
    fr.merge(wh, a, b, actor="test", reason="same plant")
    db = _db(wh, tmp_path)
    key = [k for k, f in db.keys.items() if f == a][0]
    assert db.get(key) == b
    with pytest.raises(fr.RegistryConflict):
        fr.merge(wh, a, b, actor="test", reason="again")           # a merged facility is not live


def test_merge_is_always_one_hop(wh, tmp_path):
    ids = _seeded(wh, tmp_path)
    c = fr.mint(wh, actor="test", reason="third plant")["minted"]
    a, b = ids["Legacy Building Solutions"], ids["Aura Prefab, LLC"]
    fr.merge(wh, a, b, actor="t", reason="r")
    fr.merge(wh, b, c, actor="t", reason="r")
    roots = {r["facility_id"]: r["merged_into"] for r in wh.query("SELECT facility_id, merged_into FROM facility")}
    assert roots[a] == c and roots[b] == c


def test_repoint_and_retire_are_events(wh, tmp_path):
    ids = _seeded(wh, tmp_path)
    a, b = ids["Legacy Building Solutions"], ids["Aura Prefab, LLC"]
    key = wh.query("SELECT match_key FROM facility_match_key WHERE facility_id = ?", (a,))[0]["match_key"]
    fr.repoint(wh, key, b, actor="t", reason="wrong plant")
    assert wh.query("SELECT facility_id FROM facility_match_key WHERE match_key = ?", (key,)) == [{"facility_id": b}]
    fr.retire(wh, a, actor="t", reason="closed")
    kinds = {r["kind"] for r in wh.query("SELECT kind FROM facility_event")}
    assert {"seed", "repoint", "retire"} <= kinds


def test_save_refuses_to_repoint_a_key_another_writer_claimed(wh, tmp_path):
    _seeded(wh, tmp_path)
    db = _db(wh, tmp_path)
    fid = db.get("ZZ|S|contested st")
    other = fr.mint(wh, actor="elsewhere", reason="concurrent")["minted"]
    with wh.transaction() as c:
        c.execute("INSERT INTO facility_match_key (match_key, facility_id, method, confidence, source, first_seen) "
                  "VALUES ('ZZ|S|contested st', ?, 'street_key', 0.9, 'elsewhere', 'now')", (other,))
    assert fid != other
    with pytest.raises(fr.RegistryConflict):
        db.save()
    assert not wh.query("SELECT 1 FROM facility WHERE facility_id = ?", (fid,))    # nothing written


def test_is_seeded(wh, tmp_path):
    assert not fr.is_seeded(wh)
    _seeded(wh, tmp_path)
    assert fr.is_seeded(wh)


def test_a_signature_the_file_already_numbered_keeps_that_number(wh, tmp_path):
    ids = _seeded(wh, tmp_path)
    # A T0 lead: numbered in id_registry.json long ago, never in golden, so never seeded.
    export = tmp_path / "id_registry.json"
    export.write_text(json.dumps({"next": 50, "ids": {"OK|N|tulsa|lone lead homes": "IC-00042"}}))
    rows = ROWS + [_row("ok", "Lone Lead Homes", "", "Tulsa", "OK")]
    rec = reconcile.run(normalise(rows), tmp_path / "unused.json", registry=_db(wh, tmp_path))
    got = [f for f in rec["facilities"] if f["name"] == "Lone Lead Homes"][0]["facility_id"]
    assert got == "IC-00042"
    assert rec["ids_issued"] == 0 and rec["ids_adopted"] == 1
    assert wh.query("SELECT kind FROM facility_event WHERE facility_id = 'IC-00042'") == [{"kind": "adopt"}]
    # a second run finds it registered
    again = reconcile.run(normalise(rows), tmp_path / "unused.json", registry=_db(wh, tmp_path))
    assert again["ids_issued"] == 0 and again["ids_adopted"] == 0
