"""The permanent facility registry (epic #39, #41): seeded from golden, idempotent, never renumbers."""
import json
import os
from pathlib import Path

import pytest

from pipeline import facility_registry as fr
from pipeline import warehouse

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


def _golden(wh, ids):
    with wh.transaction() as c:
        c.executemany("INSERT INTO golden_facility (facility_key, release_tag) VALUES (?, 'r1')", [(i,) for i in ids])


def _registry(tmp_path, ids: dict, nxt=5) -> Path:
    p = tmp_path / "id_registry.json"
    p.write_text(json.dumps({"next": nxt, "ids": ids}))
    return p


REG = {"WA|S|18504 canyon rd e": "IC-00001", "CO|N|grand junction|ladabuild": "IC-00002",
       "TX|S|1 main st": "IC-00003", "NFP|mobile crew": "IC-00004"}


def test_seed_keeps_every_golden_number_and_its_signatures(wh, tmp_path):
    _golden(wh, ["IC-00001", "IC-00002", "IC-00004"])
    rep = fr.seed(wh, _registry(tmp_path, REG))
    assert rep["facilities"] == {"active": 3}
    rows = {r["match_key"]: r for r in wh.query("SELECT * FROM facility_match_key")}
    assert set(rows) == {"WA|S|18504 canyon rd e", "CO|N|grand junction|ladabuild", "NFP|mobile crew"}
    assert rows["WA|S|18504 canyon rd e"]["facility_id"] == "IC-00001"
    assert rows["WA|S|18504 canyon rd e"]["method"] == "street_key"
    assert rows["CO|N|grand junction|ladabuild"]["method"] == "name+city"
    assert rows["NFP|mobile crew"]["method"] == "no-fixed-plant"
    # IC-00003 is in the registry but not in golden: it is not a permanent facility (#42 maps it).
    assert not wh.query("SELECT 1 FROM facility WHERE facility_id = 'IC-00003'")
    assert rep["events"] == 3


def test_seed_is_idempotent(wh, tmp_path):
    _golden(wh, ["IC-00001", "IC-00002"])
    reg = _registry(tmp_path, REG)
    first = fr.seed(wh, reg)
    second = fr.seed(wh, reg)
    assert (first["facilities"], first["match_keys"], first["events"]) == \
           (second["facilities"], second["match_keys"], second["events"])


def test_counter_starts_above_every_number_ever_issued_and_is_never_lowered(wh, tmp_path):
    _golden(wh, ["IC-00001"])
    rep = fr.seed(wh, _registry(tmp_path, {**REG, "OR|S|9 elm": "IC-97000"}, nxt=97001))
    assert rep["next_id"] == "IC-97001"
    # A later seed against an older registry must not pull the counter back down.
    rep = fr.seed(wh, _registry(tmp_path, REG, nxt=5))
    assert rep["next_id"] == "IC-97001"
    assert fr.status(wh)["next_id"] >= f"IC-{fr.ID_FLOOR:05d}"


def test_seed_refuses_a_golden_id_the_registry_does_not_know(wh, tmp_path):
    _golden(wh, ["IC-00001", "IC-55555"])
    with pytest.raises(fr.SeedRefused):
        fr.seed(wh, _registry(tmp_path, REG))
    assert not wh.query("SELECT 1 FROM facility")


def test_seed_refuses_to_repoint_a_key_without_an_event(wh, tmp_path):
    _golden(wh, ["IC-00001"])
    fr.seed(wh, _registry(tmp_path, REG))
    # A second registry claims the same signature for a different plant: that is a re-point.
    _golden(wh, ["IC-00009"])
    with pytest.raises(fr.SeedRefused):
        fr.seed(wh, _registry(tmp_path, {"WA|S|18504 canyon rd e": "IC-00009",
                                         "WA|N|puyallup|premier sips": "IC-00001"}))
    assert wh.query("SELECT facility_id FROM facility_match_key WHERE match_key = 'WA|S|18504 canyon rd e'") \
        == [{"facility_id": "IC-00001"}]


def test_dry_run_writes_nothing(wh, tmp_path):
    _golden(wh, ["IC-00001"])
    rep = fr.seed(wh, _registry(tmp_path, REG), dry_run=True)
    assert rep["keys_planned"] == 1
    assert not wh.query("SELECT 1 FROM facility")


def test_plan_is_pure_and_reports_what_it_refuses():
    plan = fr.plan_seed(["IC-00001", "IC-00099"], {"next": 3, "ids": REG}, {})
    assert plan["missing"] == ["IC-00099"]
    assert plan["floor"] == fr.ID_FLOOR
