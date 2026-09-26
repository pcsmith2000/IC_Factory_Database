"""Duplicate facilities at one parcel (#52): tiered, survivor chosen, merged through the registry."""
import json
import os

import pytest

from pipeline import duplicates as dup
from pipeline import facility_registry as fr
from pipeline import warehouse

PG_URL = os.environ.get("TEST_DATABASE_URL")
TABLES = ("facility_duplicate_candidate", "golden_dirty", "facility_event", "facility_match_key",
          "legacy_id_map", "golden_facility", "facility")


def row(fid, name, address, city="Phoenix", state="AZ", n=10, **kw):
    return {"facility_key": fid, "name": name, "address": address, "city": city, "state": state,
            "n_assertions": n, **kw}


def pairs(cands):
    return {(c["facility_id"], c["duplicate_of"]): c["tier"] for c in cands}


# ---------------------------------------------------------------- scan (pure)
def test_three_spellings_of_one_plant_collapse_to_one_survivor_as_certain():
    cands = dup.scan([row("IC-09911", "Cavco Industries, Inc.", "2502 W Durango St", n=40),
                      row("IC-93656", "CAVCO INDUSTRIES INC - Durango Plant", "2502 West Durango Street", n=12),
                      row("IC-95267", "Cavco", "2502 W. Durango St.", n=3)])
    assert pairs(cands) == {("IC-93656", "IC-09911"): "certain", ("IC-95267", "IC-09911"): "certain"}


def test_avenue_and_ave_are_one_parcel():
    cands = dup.scan([row("IC-00001", "Acme Modular", "100 Main Avenue"),
                      row("IC-00002", "Acme Modular LLC", "100 Main Ave, Suite 3")])
    assert pairs(cands) == {("IC-00002", "IC-00001"): "certain"}


def test_madison_drive_se_spellings_are_one_parcel():
    cands = dup.scan([row("IC-93547", "Madison Industries", "1035 Iris Drive SE", city="Decatur", state="AL"),
                      row("IC-92986", "Madison Industries of Georgia, LLC", "1035 Iris Dr. S.E.", city="Decatur", state="AL")])
    assert pairs(cands) == {("IC-93547", "IC-92986"): "certain"}        # tie on assertions: lowest number


def test_a_different_house_number_or_city_is_no_candidate():
    assert dup.scan([row("IC-00001", "Acme Modular", "100 Main St"),
                     row("IC-00002", "Acme Modular", "102 Main St"),
                     row("IC-00003", "Acme Modular", "100 Main St", city="Tempe"),
                     row("IC-00004", "Acme Modular", "100 Oak St")]) == []


def test_unrelated_operators_at_one_parcel_are_review():
    cands = dup.scan([row("IC-00001", "Acme Truss", "5 Industrial Pkwy", n=5),
                      row("IC-00002", "Zeta Steel Buildings", "5 Industrial Parkway", n=9)])
    assert pairs(cands) == {("IC-00001", "IC-00002"): "review"}
    assert cands[0]["evidence"]["reason"] == "names_unrelated"


def test_a_shared_distinctive_word_is_likely_and_a_shared_phone_is_certain():
    cands = dup.scan([row("IC-00001", "Champion Home Builders", "1 Plant Rd"),
                      row("IC-00002", "Champion Homes of Sangerfield", "1 Plant Road"),
                      row("IC-00003", "Genesis Homes", "7 Elm St", phone="(555) 123-4567"),
                      row("IC-00004", "Commodore Corp", "7 Elm Street", phone="1-555-123-4567")])
    assert pairs(cands) == {("IC-00002", "IC-00001"): "likely", ("IC-00004", "IC-00003"): "certain"}


def test_generic_words_alone_are_not_a_match():
    assert pairs(dup.scan([row("IC-00001", "Homes", "9 A St"), row("IC-00002", "Franklin Homes", "9 A St")])) \
        == {("IC-00002", "IC-00001"): "review"}


def test_a_mixed_group_still_folds_its_certain_part():
    """B and C are one firm; A is another at the same parcel and has the most assertions."""
    cands = dup.scan([row("IC-00001", "Acme Truss", "5 Mill Rd", n=50),
                      row("IC-00002", "Franklin Homes", "5 Mill Rd", n=4),
                      row("IC-00003", "Franklin Homes Inc Plant 2", "5 Mill Road", n=8)])
    assert pairs(cands) == {("IC-00002", "IC-00001"): "review", ("IC-00003", "IC-00001"): "review",
                            ("IC-00002", "IC-00003"): "certain"}


def test_names_normalise():
    assert dup.name_variants("The Cavco Industries, Inc. - Durango Plant #2") == {"cavco industries"}
    assert dup.name_variants("Palm Harbor Homes d/b/a Cavco") == {"palm harbor homes", "cavco"}


# ---------------------------------------------------------------- warehouse
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
    G = [row("IC-09911", "Cavco Industries, Inc.", "2502 W Durango St", n=40),
         row("IC-93656", "Cavco Industries", "2502 West Durango Street", n=12),
         row("IC-95267", "Cavco", "2502 W. Durango St.", n=3),
         row("IC-00001", "Acme Truss", "5 Industrial Pkwy", n=5),
         row("IC-00002", "Zeta Steel Buildings", "5 Industrial Parkway", n=9)]
    with w.transaction() as c:
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, 't', 't')", [(r["facility_key"],) for r in G])
        c.executemany("INSERT INTO golden_facility (facility_key, release_tag, name, address, city, state, n_assertions) "
                      "VALUES (?, 'r1', ?, ?, ?, ?, ?)",
                      [(r["facility_key"], r["name"], r["address"], r["city"], r["state"], r["n_assertions"]) for r in G])
    yield w
    w.close()


def cand_rows(wh):
    return {(r["facility_id"], r["duplicate_of"]): r for r in wh.query("SELECT * FROM facility_duplicate_candidate")}


def test_dry_run_writes_nothing(wh):
    rep = dup.candidates(wh, dry_run=True)
    assert rep["by_tier"] == {"certain": 2, "likely": 0, "review": 1}
    assert cand_rows(wh) == {}
    rep = dup.candidates(wh)
    ap = dup.apply(wh, ("certain", "review"), actor="t", dry_run=True)
    assert ap["merged"] == 3
    assert {r["status"] for r in wh.query("SELECT status FROM facility")} == {"active"}
    assert {r["status"] for r in cand_rows(wh).values()} == {"pending"}


def test_candidates_are_recorded_and_idempotent(wh):
    rep = dup.candidates(wh)
    assert rep["new"] == 3
    rows = cand_rows(wh)
    assert rows[("IC-93656", "IC-09911")]["tier"] == "certain"
    assert rows[("IC-93656", "IC-09911")]["source"] == "parcel_scan"
    assert json.loads(rows[("IC-93656", "IC-09911")]["evidence"])["names"]["IC-09911"] == "Cavco Industries, Inc."
    again = dup.candidates(wh)
    assert (again["new"], again["updated"], again["unchanged"]) == (0, 0, 3)


def test_apply_merges_through_the_registry_and_is_idempotent(wh):
    dup.candidates(wh)
    rep = dup.apply(wh, ("certain",), actor="tester", dry_run=False)
    assert rep["merged"] == 2 and rep["skipped"] == 0
    fac = {r["facility_id"]: r for r in wh.query("SELECT * FROM facility")}
    assert fac["IC-93656"]["status"] == "merged" and fac["IC-93656"]["merged_into"] == "IC-09911"
    assert fac["IC-95267"]["merged_into"] == "IC-09911"
    assert fac["IC-00001"]["status"] == "active"                     # review tier untouched
    ev = wh.query("SELECT * FROM facility_event WHERE kind = 'merge' ORDER BY facility_id")
    assert [e["facility_id"] for e in ev] == ["IC-93656", "IC-95267"]
    assert ev[0]["actor"] == "tester" and ev[0]["reason"].startswith("duplicate: certain same parcel")
    rows = cand_rows(wh)
    assert rows[("IC-93656", "IC-09911")]["status"] == "merged"
    assert rows[("IC-93656", "IC-09911")]["decided_by"] == "tester"
    assert {("IC-93656", ""), ("IC-09911", "")} <= {(r["facility_key"], r["release_tag"]) for r in wh.query("SELECT * FROM golden_dirty")}
    again = dup.apply(wh, ("certain",), actor="tester", dry_run=False)
    assert again["merged"] == 0 and again["pending_in_tiers"] == 0
    assert len(wh.query("SELECT * FROM facility_event WHERE kind = 'merge'")) == 2


def test_apply_skips_a_pair_whose_side_is_no_longer_active_and_marks_a_done_merge(wh):
    dup.candidates(wh)
    fr.retire(wh, "IC-95267", "t", "closed")
    fr.merge(wh, "IC-93656", "IC-09911", "someone", "by hand")
    rep = dup.apply(wh, ("certain",), actor="t", dry_run=False, limit=5)
    assert rep["merged"] == 0 and rep["marked_already_merged"] == 1 and rep["skipped"] == 1
    assert rep["skips"][0]["why"] == "IC-95267 is retired"
    rows = cand_rows(wh)
    assert rows[("IC-93656", "IC-09911")]["status"] == "merged"
    assert rows[("IC-95267", "IC-09911")]["status"] == "pending"


def test_limit(wh):
    dup.candidates(wh)
    assert dup.apply(wh, ("certain",), actor="t", dry_run=False, limit=1)["merged"] == 1
    assert dup.apply(wh, ("certain",), actor="t", dry_run=False)["merged"] == 1


def test_a_rejected_pair_is_not_proposed_again(wh):
    dup.candidates(wh)
    dup.decide(wh, "IC-00001", "IC-00002", "rejected", actor="peter", reason="two firms")
    rep = dup.candidates(wh)
    assert rep["already_decided"] == {"rejected": 1} and rep["new"] == 0
    r = cand_rows(wh)[("IC-00001", "IC-00002")]
    assert (r["status"], r["decided_by"]) == ("rejected", "peter")
    assert dup.apply(wh, ("review",), actor="t", dry_run=False)["merged"] == 0
    # a pair rejected before any scan found it stays rejected when the scan does find it
    dup.decide(wh, "IC-95267", "IC-09911", "rejected", actor="peter")
    with wh.transaction() as c:
        c.execute("DELETE FROM facility_duplicate_candidate WHERE facility_id = 'IC-93656'")
    rep = dup.candidates(wh)
    assert cand_rows(wh)[("IC-95267", "IC-09911")]["status"] == "rejected" and rep["new"] == 1


def test_a_scan_leaves_research_rows_alone(wh):
    with wh.transaction() as c:
        c.execute("INSERT INTO facility_duplicate_candidate (facility_id, duplicate_of, source, tier, evidence, created_at) "
                  "VALUES ('IC-00001', 'IC-00002', 'web_research', 'likely', '{\"urls\": [\"x\"]}', 't')")
    rep = dup.candidates(wh)
    assert rep["already_decided"] == {"pending_from_web_research": 1}
    r = cand_rows(wh)[("IC-00001", "IC-00002")]
    assert (r["source"], r["tier"], r["evidence"]) == ("web_research", "likely", '{"urls": ["x"]}')


def test_decide_merged_merges_through_the_registry(wh):
    out = dup.decide(wh, "IC-00001", "IC-00002", "merged", actor="peter", reason="successor firm")
    assert (out["status"], out["source"]) == ("merged", "operator")
    f = wh.query("SELECT status, merged_into FROM facility WHERE facility_id = 'IC-00001'")[0]
    assert (f["status"], f["merged_into"]) == ("merged", "IC-00002")


def test_merged_facilities_leave_the_next_scan(wh):
    dup.candidates(wh)
    dup.apply(wh, ("certain",), actor="t", dry_run=False)
    rep = dup.candidates(wh)
    assert rep["candidates"] == 1 and rep["by_tier"]["review"] == 1


def test_cli(wh, tmp_path, capsys):
    if wh.engine != "sqlite":
        pytest.skip("the CLI's --db is sqlite")
    db = str(wh.path)
    assert dup.main(["candidates", "--db", db]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 3
    assert dup.main(["--db", db, "apply", "--tier", "certain", "--actor", "cli", "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["merged"] == 2
    assert dup.main(["decide", "IC-00001", "--of", "IC-00002", "--status", "rejected", "--actor", "cli", "--db", db]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"


def test_apply_can_take_one_sources_reviewed_list_and_leave_the_rest(wh):
    dup.candidates(wh)                                   # parcel_scan: 2 certain, 1 review
    with wh.transaction() as c:                          # two web_research pairs, same tier as a scan pair
        c.execute("DELETE FROM facility_duplicate_candidate WHERE facility_id = 'IC-95267' AND source = 'parcel_scan'")
        c.executemany("INSERT INTO facility_duplicate_candidate (facility_id, duplicate_of, source, tier, evidence, "
                      "created_at) VALUES (?, ?, 'web_research', 'likely', '{}', 't')",
                      [("IC-00002", "IC-00001"), ("IC-95267", "IC-09911")])
    rep = dup.apply(wh, ("likely", "certain", "review"), actor="t", dry_run=False,
                    sources=("web_research",), skip=("IC-00002",))
    assert [m["facility_id"] for m in rep["merges"]] == ["IC-95267"] and rep["skip"] == ["IC-00002"]
    rows = cand_rows(wh)
    assert rows[("IC-00002", "IC-00001")]["status"] == "pending"          # held for a person
    assert rows[("IC-93656", "IC-09911")]["status"] == "pending"          # another source: untouched
    rep = dup.apply(wh, ("certain",), actor="t", dry_run=True, only=("IC-93656",))
    assert [m["facility_id"] for m in rep["merges"]] == ["IC-93656"]


def test_cli_filters(wh, capsys):
    if wh.engine != "sqlite":
        pytest.skip("the CLI's --db is sqlite")
    dup.candidates(wh)
    assert dup.main(["apply", "--db", str(wh.path), "--actor", "t", "--dry-run", "--source", "web_research",
                     "--only", "IC-93656,IC-95267", "--skip", "IC-95267"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["sources"], out["only"], out["skip"], out["merged"]) == (["web_research"], 2, ["IC-95267"], 0)
