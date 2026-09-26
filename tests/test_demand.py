"""Demand research: the verification rules, merging, and the load plan (no network, no database)."""
import json, sqlite3

from pipeline.demand import load
from pipeline.demand.pipeline import (build_project, group_mentions, load_config, match_key, names_company, select,
                                      value_in_quote, verify)

VBC = {"key": "vbc", "company": "Volumetric Building Companies", "aliases": ["VBC"], "facility_ids": ["IC-94954"]}
PAGE = {"url": "https://news.example/a", "kind": "third_party", "fetched_at": "2026-09-26T00:00:00Z",
        "text": "The 120-unit Parkside Apartments in Camden, New Jersey, built by VBC with 96 modules, "
                "was completed in 2023. Developer: Acme Housing."}


def f(value, quote):
    return {"value": value, "quote": quote}


def test_verify_keeps_quoted_values_and_drops_the_rest():
    ans = {"projects": [{"name": f("Parkside Apartments", "Parkside Apartments"),
                         "units": f(120, "120-unit Parkside"),
                         "state": f("NJ", "Camden, New Jersey"),
                         "city": f("Trenton", "Camden, New Jersey"),                 # value not in its quote
                         "developer": f("Acme Housing", "Developer: Acme Housing Inc"),  # quote not on the page
                         "status": f("completed", "was completed in 2023"),
                         "segment": f("condo", "Apartments"),                         # outside the vocabulary
                         "tie": {"quote": "built by VBC with 96 modules"}}]}
    kept, dropped = verify(ans, PAGE, VBC)
    assert len(kept) == 1
    assert set(kept[0]["fields"]) == {"name", "units", "state", "status"}
    assert {d["reason"] for d in dropped} == {"value not in quote", "quote not verbatim", "value outside the vocabulary"}


def test_a_third_party_project_needs_a_tie_naming_the_company():
    ans = {"projects": [{"name": f("Parkside Apartments", "Parkside Apartments"),
                         "tie": {"quote": "was completed in 2023"}}]}
    kept, dropped = verify(ans, PAGE, VBC)
    assert kept == [] and dropped[0]["reason"] == "no verified tie to the manufacturer"
    own = dict(PAGE, kind="manufacturer_site")          # on the company's own site the heading is the tie
    kept, _ = verify(ans, own, VBC)
    assert len(kept) == 1


def test_a_project_without_a_verified_name_is_dropped():
    ans = {"projects": [{"name": f("Riverside Lofts", "Riverside Lofts"), "tie": {"quote": "built by VBC"}}]}
    kept, dropped = verify(ans, PAGE, VBC)
    assert kept == [] and dropped[-1]["reason"] == "no verified name"


def test_numbers_and_states():
    assert value_in_quote("units", 1200, "a 1,200-unit community")
    assert not value_in_quote("units", 120, "a 1,200-unit community")
    assert value_in_quote("stories", 4, "four stories of modules")
    assert value_in_quote("state", "CA", "San Jose, California")
    assert not value_in_quote("state", "CA", "San Jose")


def test_short_aliases_match_only_as_words():
    assert names_company("modules by VBC were set", VBC)
    assert not names_company("the NVBCX index", VBC)


def test_select_skips_third_party_pages_that_do_not_name_the_company():
    cfg = load_config(None)
    pages = [dict(PAGE), {"url": "https://other.example/b", "kind": "third_party",
                          "text": "A 200-unit apartment building with 150 modules by another firm."}]
    chosen, skipped = select(pages, VBC, cfg)
    assert [p["url"] for p in chosen] == [PAGE["url"]]
    assert skipped[0]["reason"] == "does not name the company"


def mention(url, kind="third_party", **fields):
    return {"url": url, "source_kind": kind, "fetched_at": None, "tie": "by VBC",
            "fields": {k: {"value": v, "quote": str(v)} for k, v in fields.items()}}


def test_merge_groups_by_key_without_a_model_and_keeps_conflicts():
    cfg = load_config(None); cfg["merge"]["model"] = None
    ms = [mention("https://a", name="Parkside Apartments", state="NJ", units=120),
          mention("https://b", name="Parkside", state="NJ", units=118),
          mention("https://c", name="Harbor Point", state="NJ")]
    assert match_key(ms[0]["fields"]) == match_key(ms[1]["fields"])
    groups = group_mentions(ms, VBC, cfg, meter=None, folder=None)
    assert sorted(map(len, groups)) == [1, 2]
    p = build_project(VBC, [ms[i] for i in next(g for g in groups if len(g) == 2)])
    assert p["conflicts"] == ["units"] and p["n_pages"] == 2 and p["third_party_pages"] == 2
    assert {e["value"] for e in p["fields"]["units"]} == {120, 118}


def doc(*projects):
    return {"summary": {"config": "v0"}, "projects": list(projects)}


def test_load_plan_mints_ids_and_matches_a_project_found_again():
    ms = [mention("https://a", name="Parkside Apartments", state="NJ", units=120)]
    p = build_project(VBC, ms)
    first = load.plan(doc(p), {}, 1, "demand-1", "2026-09-26T00:00:00Z")
    assert [x["project_id"] for x in first["projects"]] == ["DP-000001"]
    fields = {a["field"] for a in first["assertions"]}
    assert {"name", "state", "units", "supplier"} <= fields
    sup = next(a for a in first["assertions"] if a["field"] == "supplier")
    assert sup["value"] == "Volumetric Building Companies" and json.loads(sup["facility_ids"]) == ["IC-94954"]
    keys = {k["match_key"]: k["project_id"] for k in first["keys"]}
    again = load.plan(doc(build_project(VBC, [mention("https://z", name="The Parkside", state="NJ", units=121)])),
                      keys, 2, "demand-2", "2026-09-27T00:00:00Z")
    assert again["projects"] == [] and again["matched"][0]["project_id"] == "DP-000001"
    assert any(a["field"] == "units" and a["value"] == "121" for a in again["assertions"])


def test_load_statements_run_and_are_idempotent_on_sqlite():
    db = sqlite3.connect(":memory:")
    for s in load.DDL:
        db.execute(s)
    p = load.plan(doc(build_project(VBC, [mention("https://a", name="Parkside Apartments", state="NJ", units=120)])),
                  {}, 1, "demand-1", "2026-09-26T00:00:00Z")
    for _ in range(2):
        for sql, params in load.statements(p):
            db.execute(sql.replace("%s", "?"), params)
    assert db.execute("SELECT count(*) FROM demand_project").fetchone()[0] == 1
    assert db.execute("SELECT review_status FROM demand_project").fetchone()[0] == "proposed"
    assert db.execute("SELECT count(*) FROM demand_assertion").fetchone()[0] == len(p["assertions"])
