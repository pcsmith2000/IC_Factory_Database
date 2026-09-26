"""Web-research ingestion: an external agent's cited findings, validated, into the assertion table."""
import json
import os

import pytest

from pipeline import golden_refresh as gr
from pipeline import warehouse
from pipeline.web_research import ingest as I

PG_URL = os.environ.get("TEST_DATABASE_URL")
NOW = "v1+reg.a+ids.00000000+ctl.x"
TABLES = ("web_research_submission", "golden_dirty", "facility_event", "facility_match_key", "legacy_id_map",
          "release_registry", "golden_facility", "fact_assertions", "ref_source_row", "facility")
FIELDS, TAX = set(I.assertable_fields()), I._taxonomy()


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
            c.execute("DELETE FROM fact_assertions WHERE source_key = 'web_research'")
    with w.transaction() as c:
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, 't', 't')", [("IC-00001",), ("IC-00002",)])
        c.executemany("INSERT INTO golden_facility (facility_key, release_tag, name, name__source) VALUES (?, ?, ?, 'x')",
                      [("IC-00001", NOW, "American Precast"), ("IC-00002", NOW, "Cavco Office")])
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, ?, 'fl_dbpr', ?, ?, 'A', 'on_current_list', '2026-09-01')",
                      [("r1", NOW, "IC-00001", "name", "American Precast"), ("r2", NOW, "IC-00001", "address", "1980 Hughey Kimal Dr"),
                       ("r3", NOW, "IC-00001", "state", "FL"), ("r4", NOW, "IC-00002", "name", "Cavco Office"),
                       ("r5", NOW, "IC-00002", "state", "AZ")])
    gr.refresh(w, all_facilities=True)
    yield w
    w.close()


REGISTRY = {"source_ref": "s1", "url": "https://www.myfloridalicense.com/detail?id=1", "title": "License detail",
            "kind": "government_registry", "found_by": "subagent via GPT web search", "retrieved_at": "2026-09-25"}
SITE = {"source_ref": "s2", "url": "https://americanprecastcorp.com/contact", "title": "Contact",
        "kind": "company_site", "found_by": "subagent via GPT web search", "retrieved_at": "2026-09-25"}
MAPS = {"source_ref": "s3", "url": "https://maps.example.com/place/1", "title": "Map", "kind": "map_listing",
        "found_by": "subagent via Google Maps", "retrieved_at": "2026-09-25"}


def payload(fid="IC-00001", verdict=None, assertions=(), sources=(REGISTRY, SITE, MAPS)):
    return {"facility_id": fid, "agent": "test", "run_id": "t1", "verdict": verdict or {"status": "in_scope"},
            "sources": list(sources), "assertions": list(assertions)}


def A(field, value, ref, quote, conf=0.8):
    return {"field": field, "value": value, "source_ref": ref, "quote": quote, "confidence": conf}


def submit(wh, p, sid=None):
    sid = sid or f"t1:{p['facility_id']}:{len(wh.query('SELECT 1 FROM web_research_submission'))}"
    with wh.transaction() as c:
        c.execute("INSERT INTO web_research_submission (submission_id, facility_id, run_id, agent, submitted_at, payload) "
                  "VALUES (?, ?, 't1', 'test', '2026-09-25T00:00:00', ?)", (sid, p["facility_id"], json.dumps(p)))
    return sid


def golden(wh):
    return {r["facility_key"]: r for r in wh.query("SELECT * FROM golden_facility")}


def plan(p, active=("IC-00001", "IC-00002")):
    return I.plan(p, p["facility_id"], set(active), FIELDS, TAX)


# ---------------------------------------------------------------- the validator (pure)
def test_every_golden_column_but_the_owned_ones_is_assertable():
    assert {"address", "capability_leaf", "sq_ft", "website", "email"} <= FIELDS
    assert not FIELDS & I.NOT_ASSERTABLE


def test_a_finding_needs_a_quote_that_states_the_value():
    p = plan(payload(assertions=[A("zip", "34292", "s1", "VENICE Florida 34292"),
                                 A("address", "10980 Hughey Kimal Dr", "s1", "no address here"),
                                 A("phone", "9414241776", "s2", "")]))
    assert [f["field"] for f in p["facts"] if not f.get("is_source_tag")] == ["zip"]
    reasons = {r["field"]: r["reason"] for r in p["rejected"]}
    assert "does not state" in reasons["address"] and "quote" in reasons["phone"]


def test_confidence_is_capped_by_the_documents_veracity_and_sets_the_basis():
    p = plan(payload(assertions=[A("address", "10980 Hughey Kimal Dr", "s1", "10980 HUGHEY KIMAL DR.\nVENICE"),
                                 A("phone", "941-424-1776", "s2", "Call 941-424-1776", conf=0.95),
                                 A("zip", "34292", "s3", "Venice, FL 34292"),
                                 A("material", "concrete", "s1", "precast concrete plank")]))
    got = {f["field"]: (f["basis"], f["confidence"]) for f in p["facts"] if not f.get("is_source_tag")}
    assert got["address"] == ("web_verified", 0.8)
    assert got["phone"] == ("web_primary", 0.7)
    assert got["zip"] == ("web_cited", 0.6)              # a map listing only fills a blank
    assert got["material"] == ("web_inferred", 0.6)      # a judgement, never an override


def test_a_website_is_the_site_not_a_page_on_it():
    p = plan(payload(assertions=[A("website", "https://americanprecastcorp.com/about/team", "s2", "About us")]))
    assert p["facts"][0]["value"] == "https://americanprecastcorp.com"


def test_invalid_values_and_unknown_fields_are_refused():
    p = plan(payload(assertions=[A("state", "Florida", "s1", "Florida"), A("existence_flag", "active", "s1", "open"),
                                 A("capability_leaf", "Spaceships", "s2", "Spaceships"), A("zip", "34292", "s9", "34292")]))
    assert not [f for f in p["facts"] if not f.get("is_source_tag")] and len(p["rejected"]) == 4


def test_every_used_document_is_itself_on_record():
    p = plan(payload(assertions=[A("zip", "34292", "s1", "VENICE Florida 34292")]))
    tags = [f for f in p["facts"] if f.get("is_source_tag")]
    assert [(t["field"], t["value"], t["basis"]) for t in tags] == [("research_source", REGISTRY["url"], "source:government_registry")]


def test_an_exclusion_verdict_needs_a_reason_and_a_cited_source():
    assert plan(payload(verdict={"status": "not_ic", "reason": "short", "source_refs": ["s1"]}))["rejected"]
    assert plan(payload(verdict={"status": "closed", "reason": "Plant closed in 2019 per county records"}))["rejected"]
    p = plan(payload(verdict={"status": "closed", "reason": "Plant closed in 2019 per county records",
                              "source_refs": ["s3", "s1"], "confidence": 0.9}))
    flag = [f for f in p["facts"] if f["field"] == "existence_flag"][0]
    assert (flag["value"], flag["basis"], flag["confidence"], flag["source"]["ref"]) == ("closed", "web_verdict", 0.8, "s1")


def test_a_duplicate_verdict_must_name_another_active_facility():
    ok = plan(payload(verdict={"status": "duplicate", "duplicate_of": "IC-00002", "reason": "Same plant, same address",
                               "source_refs": ["s1"]}))
    assert ok["duplicate"]["duplicate_of"] == "IC-00002" and not ok["rejected"]
    bad = plan(payload(verdict={"status": "duplicate", "duplicate_of": "IC-00001", "reason": "Same plant, same address",
                                "source_refs": ["s1"]}))
    assert bad["duplicate"] is None and bad["rejected"]


def test_an_unknown_facility_is_refused_whole():
    p = plan(payload(fid="IC-99999"))
    assert p["fatal"]


# ---------------------------------------------------------------- against a warehouse
def test_ingest_writes_facts_with_their_provenance(wh):
    submit(wh, payload(assertions=[A("address", "10980 Hughey Kimal Dr", "s1", "10980 HUGHEY KIMAL DR.\nVENICE"),
                                   A("email", "info@americanprecast.co", "s2", "Email info@americanprecast.co")]))
    rep = I.ingest(wh)
    assert (rep["ingested"], rep["facts_written"], rep["sources_written"], rep["overrides"]) == (1, 2, 2, 2)
    prov = {r["field_key"]: r for r in wh.query(
        "SELECT a.field_key, a.value, a.basis, a.confidence, r.source_url, r.source_document FROM fact_assertions a "
        "JOIN ref_source_row r ON r.row_hash = a.row_hash WHERE a.source_key = 'web_research'")}
    assert prov["address"]["source_url"] == REGISTRY["url"] and "HUGHEY" in prov["address"]["source_document"]
    assert prov["research_source"]["value"] in (REGISTRY["url"], SITE["url"])
    assert wh.query("SELECT status FROM web_research_submission")[0]["status"] == "ingested"


def test_a_registry_backed_correction_overrides_the_roster_but_a_map_listing_does_not(wh):
    submit(wh, payload(assertions=[A("address", "10980 Hughey Kimal Dr", "s1", "10980 HUGHEY KIMAL DR.\nVENICE"),
                                   A("name", "American Precast of Anywhere", "s3", "American Precast of Anywhere")]))
    I.ingest(wh)
    gr.refresh(wh)
    g = golden(wh)["IC-00001"]
    assert (g["address"], g["address__source"]) == ("10980 Hughey Kimal Dr", "web_research")
    assert g["name"] == "American Precast"


def test_a_person_still_outranks_web_research(wh):
    submit(wh, payload(assertions=[A("address", "10980 Hughey Kimal Dr", "s1", "10980 HUGHEY KIMAL DR.\nVENICE")]))
    I.ingest(wh)
    with wh.transaction() as c:
        c.execute("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                  "source_class, basis, date_key) VALUES ('h1', ?, 'IC-00001', 'adl_employee_feedback', 'address', "
                  "'1 Person St', 'human_feedback', 'employee', '2026-09-02')", (NOW,))
    gr.refresh(wh)
    assert golden(wh)["IC-00001"]["address"] == "1 Person St"


def test_a_cited_not_ic_verdict_removes_the_facility_and_a_person_brings_it_back(wh):
    submit(wh, payload(fid="IC-00002", verdict={"status": "not_ic", "reason": "The address is Cavco's head office",
                                                "source_refs": ["s1"]}))
    rep = I.ingest(wh)
    assert rep["exclusions"] == 1
    gr.refresh(wh)
    assert "IC-00002" not in golden(wh)
    with wh.transaction() as c:
        c.execute("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                  "source_class, basis, date_key) VALUES ('h2', ?, 'IC-00002', 'adl_employee_feedback', 'existence_flag', "
                  "'active', 'human_feedback', 'employee', '2026-09-26')", (NOW,))
    gr.refresh(wh)
    assert "IC-00002" in golden(wh)


def test_partial_rejected_dry_run_and_idempotence(wh):
    good = payload(assertions=[A("zip", "34292", "s1", "VENICE Florida 34292"), A("zip", "342", "s1", "342")])
    submit(wh, good, "g")
    submit(wh, payload(fid="IC-99999"), "bad")
    assert I.ingest(wh, dry_run=True)["partial"] == 1
    assert not wh.query("SELECT 1 FROM fact_assertions WHERE source_key = 'web_research'")
    rep = I.ingest(wh)
    assert (rep["partial"], rep["rejected"]) == (1, 1)
    n = len(wh.query("SELECT 1 FROM fact_assertions WHERE source_key = 'web_research'"))
    with wh.transaction() as c:          # the same findings submitted again write nothing new
        c.execute("UPDATE web_research_submission SET status = 'pending' WHERE submission_id = 'g'")
    I.ingest(wh)
    assert len(wh.query("SELECT 1 FROM fact_assertions WHERE source_key = 'web_research'")) == n
    report = json.loads(wh.query("SELECT report FROM web_research_submission WHERE submission_id = 'bad'")[0]["report"])
    assert "not an active" in report["rejected"][0]["reason"]
