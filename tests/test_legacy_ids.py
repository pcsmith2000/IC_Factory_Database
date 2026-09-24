"""The legacy crosswalk (#42): every historical IC-number -> the permanent facility it meant."""
import json

import pytest

from pipeline import facility_registry as fr
from pipeline import legacy_ids as L
from pipeline import warehouse

NOW = "v1+reg.a+ids.00000000+ctl.x"      # the current golden release, registry 00000000
OLD_A = "v1+reg.b+ids.aaaaaaaa+ctl.x"    # a release from a diverged registry
OLD_A2 = "v1+reg.c+ids.aaaaaaaa+ctl.y"   # another release from that same registry
OLD_B = "v1+reg.d+ids.bbbbbbbb+ctl.x"


def ident(fid, tag, name=None, city=None, state=None, address=None):
    out = []
    for field, value in (("name", name), ("city", city), ("state", state), ("address", address)):
        if value:
            out.append({"facility_id": fid, "release_tag": tag, "field": field, "value": value})
    return out


# Current release: three permanent plants.
CURRENT = (ident("IC-00001", NOW, "Ladabuild", "Grand Junction", "CO", "123 Main St")
           + ident("IC-00002", NOW, "Blueprint Robotics", "Baltimore", "MD", "9 Harbor Rd")
           + ident("IC-00003", NOW, "Dukane Precast", "Naperville", "IL"))
PERMANENT = {"IC-00001", "IC-00002", "IC-00003"}


def by_pair(rows):
    return {(r["registry_hash"], r["legacy_id"]): r for r in rows}


def test_registry_hash_comes_from_the_release_tag():
    assert L.registry_hash(OLD_A) == "aaaaaaaa"
    assert L.registry_hash("v1+reg.x") == ""


def test_a_number_that_meant_different_plants_splits_across_registries():
    # IC-00001 was Blueprint Robotics in registry aaaaaaaa: it maps to Blueprint's permanent id,
    # not to Ladabuild, which holds the number now.
    rows = CURRENT + ident("IC-00001", OLD_A, "Blueprint Robotics", "Baltimore", "MD")
    out = by_pair(L.resolve([("IC-00001", NOW), ("IC-00001", OLD_A)], rows, NOW, PERMANENT))
    assert out[("00000000", "IC-00001")]["method"] == "current"
    assert out[("aaaaaaaa", "IC-00001")]["facility_id"] == "IC-00002"
    assert out[("aaaaaaaa", "IC-00001")]["method"] == "identity"


def test_same_number_same_plant_and_releases_of_one_registry_pool_their_evidence():
    # OLD_A2 asserts no name for IC-00002, but OLD_A (same registry) does: one row, resolved.
    rows = CURRENT + ident("IC-00002", OLD_A, "BLUEPRINT ROBOTICS", "Baltimore", "MD")
    out = L.resolve([("IC-00002", OLD_A), ("IC-00002", OLD_A2)], rows, NOW, PERMANENT)
    assert len(out) == 1
    assert (out[0]["facility_id"], out[0]["method"]) == ("IC-00002", "same_id")


def test_unique_name_without_a_city_resolves():
    rows = CURRENT + ident("IC-00077", OLD_B, "Ladabuild")
    out = L.resolve([("IC-00077", OLD_B)], rows, NOW, PERMANENT)[0]
    assert (out["facility_id"], out["method"]) == ("IC-00001", "unique_name")


def test_same_name_in_a_different_city_is_a_second_plant_not_a_match():
    rows = CURRENT + ident("IC-00088", OLD_B, "Dukane Precast", "Aurora", "IL")
    out = L.resolve([("IC-00088", OLD_B)], rows, NOW, PERMANENT)[0]
    assert (out["facility_id"], out["method"]) == (None, "unresolved")


def test_unique_name_in_another_state_is_refused():
    rows = CURRENT + ident("IC-00089", OLD_B, "Ladabuild", state="TX")
    assert L.resolve([("IC-00089", OLD_B)], rows, NOW, PERMANENT)[0]["method"] == "unresolved"


def test_a_numbered_street_address_follows_the_parcel():
    rows = CURRENT + ident("IC-00090", OLD_B, "Old Owner Homes", "Grand Junction", "CO", "123 Main St")
    out = L.resolve([("IC-00090", OLD_B)], rows, NOW, PERMANENT)[0]
    assert (out["facility_id"], out["method"]) == ("IC-00001", "address")


def test_ambiguity_never_resolves():
    two = CURRENT + ident("IC-00004", NOW, "Ladabuild", "Grand Junction", "CO")
    rows = two + ident("IC-00091", OLD_B, "Ladabuild", "Grand Junction", "CO")
    out = L.resolve([("IC-00091", OLD_B)], rows, NOW, PERMANENT | {"IC-00004"})[0]
    assert out["method"] == "unresolved" and out["facility_id"] is None


def test_a_number_with_no_identity_is_unresolved_and_never_guessed():
    out = L.resolve([("IC-00092", OLD_B)], CURRENT, NOW, PERMANENT)[0]
    assert out["method"] == "unresolved"


# ---------------------------------------------------------------- against a warehouse
@pytest.fixture
def wh(tmp_path):
    w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    yield w
    w.close()


def _facts(wh, rows):
    with wh.transaction() as c:
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value) "
                      "VALUES (?, ?, ?, 'src', ?, ?)",
                      [(f"a{i}", r["release_tag"], r["facility_id"], r["field"], r["value"]) for i, r in enumerate(rows)])


def test_crosswalk_writes_the_map_and_the_view_resolves_every_fact(wh, tmp_path):
    old = ident("IC-00001", OLD_A, "Blueprint Robotics", "Baltimore", "MD") + ident("IC-00099", OLD_B, "Gone Plant", "Nowhere", "ND")
    _facts(wh, CURRENT + old)
    with wh.transaction() as c:
        c.executemany("INSERT INTO golden_facility (facility_key, release_tag) VALUES (?, ?)", [(f, NOW) for f in sorted(PERMANENT)])
    reg = tmp_path / "id_registry.json"
    reg.write_text(json.dumps({"next": 4, "ids": {"CO|S|123 main st": "IC-00001", "MD|S|9 harbor rd": "IC-00002",
                                                  "IL|N|naperville|dukane precast": "IC-00003"}}))
    fr.seed(wh, reg)
    assert L.crosswalk(wh, dry_run=True)["dry_run"] and not wh.query("SELECT 1 FROM legacy_id_map")
    rep = L.crosswalk(wh)
    assert rep["by_method"] == {"current": 3, "identity": 1, "unresolved": 1}
    got = {(r["release_tag"], r["facility_key"], r["field_key"]): r["permanent_facility_id"]
           for r in wh.query("SELECT release_tag, facility_key, field_key, permanent_facility_id FROM v_assertions_resolved")}
    assert got[(OLD_A, "IC-00001", "name")] == "IC-00002"      # the chimera, pointed at the right plant
    assert got[(NOW, "IC-00001", "name")] == "IC-00001"
    assert got[(OLD_B, "IC-00099", "name")] is None             # a plant no longer held: no permanent id
    # Idempotent: a second run replaces the map with the same map.
    assert L.crosswalk(wh)["by_method"] == rep["by_method"]
    assert len(wh.query("SELECT * FROM legacy_id_map")) == 5


def test_crosswalk_refuses_before_the_registry_is_seeded(wh):
    _facts(wh, CURRENT)
    with wh.transaction() as c:
        c.execute("INSERT INTO golden_facility (facility_key, release_tag) VALUES ('IC-00001', ?)", (NOW,))
    with pytest.raises(RuntimeError, match="seed"):
        L.crosswalk(wh)


def test_same_name_at_a_different_street_is_a_second_plant_not_a_match():
    # TrueNorth Steel: the Fargo plant was recorded with a street but no city or state; the only
    # live plant of that name is in Mandan at another street. Not the same plant.
    rows = (ident("IC-00005", NOW, "TrueNorth Steel", "Mandan", "ND", "2522 Memorial Highway")
            + ident("IC-00006", OLD_B, "TrueNorth Steel", address="4401 Main Ave."))
    out = L.resolve([("IC-00006", OLD_B)], rows, NOW, {"IC-00005"})[0]
    assert (out["facility_id"], out["method"]) == (None, "unresolved")
    # the same record at the same street is the same plant
    rows2 = rows[:-1] + ident("IC-00006", OLD_B, "TrueNorth Steel", address="2522 Memorial Highway")
    assert L.resolve([("IC-00006", OLD_B)], rows2, NOW, {"IC-00005"})[0]["facility_id"] == "IC-00005"


def test_the_same_street_in_another_spelling_is_the_same_plant():
    # Great Outdoor Cottages: "21498 BALTIMORE AVENUE" then, "21498 Baltimore Ave" now.
    rows = (ident("IC-00007", NOW, "Great Outdoor Cottages LLC", address="21498 Baltimore Ave")
            + ident("IC-00008", OLD_B, "Great Outdoor Cottages LLC", "Georgetown", "DE", "21498 BALTIMORE AVENUE"))
    out = L.resolve([("IC-00008", OLD_B)], rows, NOW, {"IC-00007"})[0]
    assert (out["facility_id"], out["method"]) == ("IC-00007", "unique_name")


def test_another_house_number_on_the_same_street_is_another_parcel():
    rows = (ident("IC-00005", NOW, "TrueNorth Steel", "Mandan", "ND", "2522 Memorial Highway")
            + ident("IC-00006", OLD_B, "TrueNorth Steel", address="2600 Memorial Hwy"))
    assert L.resolve([("IC-00006", OLD_B)], rows, NOW, {"IC-00005"})[0]["method"] == "unresolved"
