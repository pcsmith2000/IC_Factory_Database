"""The web research evaluation harness: quote check, cost ceiling, normalised comparisons, a
deterministic split, no warehouse writes, and one offline pass end to end (mocked web and gateway)."""
import json
import re
from pathlib import Path

import pytest

from pipeline.research_eval import benchmark as B
from pipeline.research_eval import gateway as gw
from pipeline.research_eval import pipeline as P
from pipeline.research_eval import score as S

PKG = Path(B.__file__).parent
PRICES = {"cheap/model": {"input": 1e-7, "output": 2e-7, "per_million": {"input": 0.1, "output": 0.2}}}


# --- quote check --------------------------------------------------------------------------------

def test_quote_must_be_verbatim_after_whitespace():
    page = "Call us at\n (970) 522-2464   or visit 626 South 11th Avenue, Sterling, CO 80751"
    assert P.quote_ok("(970) 522-2464", page)
    assert P.quote_ok("626 South 11th   Avenue, Sterling", page)          # whitespace is the only allowance
    assert not P.quote_ok("626 S 11th Ave", page)                         # no expansions or rewrites
    assert not P.quote_ok("(970) 522-2465", page)
    assert not P.quote_ok("ab", page)                                     # too short to be evidence


def test_build_submission_drops_quotes_not_on_a_fetched_page():
    rec = {"facility_id": "IC-1", "name": "Acme Truss", "city": "Sterling", "state": "CO"}
    pages = [{"url": "https://acmetruss.com/contact", "title": "Contact", "fetched_at": "2026-09-26",
              "text": "Acme Truss plant: 626 South 11th Avenue, Sterling, CO 80751. Phone (970) 522-2464."}]
    psg = [{"id": "P1", "url": pages[0]["url"], "text": pages[0]["text"], "page": 0, "chunk": 0}]
    answer = {"verdict": {"status": "in_scope", "reason": "Acme Truss builds trusses in Sterling.",
                          "evidence": [{"passage": "P1", "quote": "Acme Truss plant"}]},
              "fields": {"phone": {"value": "9705222464", "passage": "P1", "quote": "Phone (970) 522-2464"},
                         "zip": {"value": "80751", "passage": "P1", "quote": "Sterling, CO 80751"},
                         "email": {"value": "a@acme.com", "passage": "P1", "quote": "Email a@acme.com"}}}
    cfg = P.merge(P.DEFAULT_CONFIG, {"regex": {"fill": False}})
    payload, trace = P.build_submission(rec, answer, psg, pages, {}, cfg, {"IC-1"}, [], "acmetruss.com")
    assert {a["field"] for a in payload["assertions"]} == {"phone", "zip"}
    assert [d["field"] for d in trace["dropped"]] == ["email"]
    planned = P.contract(payload, "IC-1", {"IC-1"})
    assert planned["rejected"] == []


def test_removal_on_one_page_is_held_as_not_found():
    rec = {"facility_id": "IC-1", "name": "Acme", "city": "X", "state": "CO"}
    pages = [{"url": "https://dir.example.com/a", "text": "Acme closed its plant in 2019 for good.", "fetched_at": "2026-09-26"}]
    psg = [{"id": "P1", "url": pages[0]["url"], "text": pages[0]["text"]}]
    answer = {"verdict": {"status": "closed", "reason": "The directory says the plant closed in 2019.",
                          "evidence": [{"passage": "P1", "quote": "Acme closed its plant in 2019"}]}}
    payload, trace = P.build_submission(rec, answer, psg, pages, {}, P.DEFAULT_CONFIG, {"IC-1"}, [], "")
    assert payload["verdict"]["status"] == "not_found"
    assert trace["downgraded"]["from"] == "closed"


# --- cost ceiling -------------------------------------------------------------------------------

def test_meter_refuses_a_facility_that_could_cross_the_ceiling():
    m = gw.Meter(0.10, PRICES)
    plan = {"searches": 1, "calls": [("cheap/model", 10_000, 1_000, 2)]}
    worst = m.worst_case(plan)
    assert worst == pytest.approx(0.007 + 2 * (10_000 * 1e-7 + 1_000 * 2e-7))
    assert m.can_start(plan)
    m.list = 0.10 - worst / 2
    assert not m.can_start(plan)


def test_meter_prices_searches_at_list_even_when_billed_free():
    m = gw.Meter(0.10, PRICES)
    rec = m.record("search", "cheap/model", {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.0001},
                   "IC-1", searches=1, provider="tako")
    assert rec["billed_usd"] == pytest.approx(0.0001)
    assert rec["list_usd"] == pytest.approx(0.007 + 1000 * 1e-7 + 100 * 2e-7)
    m.record("judge", "cheap/model", {"prompt_tokens": 10, "completion_tokens": 10}, "IC-1")   # no cost reported
    assert m.missing_cost == 1 and m.billed > 0.0001


@pytest.mark.parametrize("bad", [0, -1, 1.01])
def test_meter_cap_is_bounded(bad):
    with pytest.raises(ValueError):
        gw.Meter(bad, PRICES)


def test_catalog_prices_take_the_dearest_regional_rate_and_flag_the_price_line():
    cat = {"data": [{"id": "a/x", "pricing": {"input": "0.000000076", "output": "0.000000153",
                                              "regional": {"us": {"input": "0.00000013", "output": "0.00000026"}}}},
                    {"id": "b/y", "pricing": {"input": "0.000003", "output": "0.000015"}}]}
    p = gw.prices(cat, ["a/x", "b/y"])
    assert p["a/x"]["per_million"] == {"input": 0.13, "output": 0.26}
    assert not gw.over_price_line(p["a/x"]) and gw.over_price_line(p["b/y"])


# --- normalised comparisons ---------------------------------------------------------------------

@pytest.mark.parametrize("field,a,b,expect", [
    ("phone", "(970) 522-2464", "+1 970.522.2464", True),
    ("phone", "970-522-2464", "970-522-2465", False),
    ("zip", "80751-1234", "80751", True),
    ("website", "https://www.acme.com/contact", "acme.com", True),
    ("website", "https://acme.com", "https://acme.net", False),
    ("address", "626 South 11th Avenue", "626 S 11th Ave", True),
    ("address", "626 South 11th Avenue", "627 South 11th Avenue", False),
    ("name", "Sterling Component Systems Inc", "Sterling Component Systems", True),
    ("name", "Sterling Component Systems", "Sterling Lumber", False),
    ("state", "Colorado", "CO", True),
    ("city", "St. Louis", "st louis", True),
])
def test_normalised_comparison(field, a, b, expect):
    assert S.same(field, a, b) is expect


# --- the split ----------------------------------------------------------------------------------

def _labels(n=900):
    verdicts = ["in_scope"] * 3 + ["not_found"] * 3 + ["not_ic", "closed", "duplicate"]
    labels, inputs = {}, {}
    for i in range(n):
        v = verdicts[i % len(verdicts)]
        labels[f"IC-{i:05d}"] = {"verdict": v, "removal_strength": ("strong" if i % 5 else "weak") if v in B.REMOVALS else None}
        inputs[f"IC-{i:05d}"] = {"website": "x.com"} if i % 3 == 0 else {}
    return labels, inputs


def test_split_is_deterministic_disjoint_and_oversamples_removals():
    labels, inputs = _labels()
    anchors = [f"IC-{i:05d}" for i in range(0, 300, 3)]
    a = B.split(labels, inputs, anchors, seed=7)
    b = B.split(dict(reversed(list(labels.items()))), inputs, list(reversed(anchors)), seed=7)
    assert a == b
    assert B.split(labels, inputs, anchors, seed=8) != a
    sizes = {k: len(v) for k, v in a.items()}
    assert sizes == {"holdout": 100, "dev_a": 25, "dev_b": 25, "dev_c": 25, "dev_d": 25, "dev_e": 25, "dev_f": 25, "anchor": 50}
    seen = [f for k, v in a.items() if k != "anchor" for f in v]
    assert len(seen) == len(set(seen))
    rem = sum(1 for f in a["dev_a"] if labels[f]["verdict"] in B.REMOVALS)
    dup = sum(1 for f in a["dev_a"] if labels[f]["verdict"] == "duplicate")
    assert (rem, dup) == (8, 4)                          # 30% and 15% of 25, rounded


def test_reference_label_keeps_only_accepted_findings_and_grades_removals():
    payload = {"verdict": {"status": "closed", "reason": "r", "source_refs": ["s1"]},
               "sources": [{"source_ref": "s1", "url": "https://a.gov/x", "kind": "government_registry"}],
               "assertions": [{"field": "phone", "value": "1", "source_ref": "s1", "quote": "q"},
                              {"field": "zip", "value": "80751", "source_ref": "s1", "quote": "80751"}]}
    lab = B.reference_label(payload, {"exclusion": "closed", "rejected": [{"item": "assertions[0]"}]}, {})
    assert [f["field"] for f in lab["findings"]] == ["zip"]
    assert lab["removal_strength"] == "strong"
    payload["sources"][0]["kind"] = "trade_directory"
    assert B.reference_label(payload, {}, {})["removal_strength"] == "weak"


def test_duplicate_survivor_follows_merges():
    facilities = {"IC-1": {"merged_into": "IC-2"}, "IC-2": {"merged_into": "IC-3"}, "IC-3": {"merged_into": None}}
    assert B.survivor("IC-1", facilities) == "IC-3"


# --- nothing writes to the warehouse ------------------------------------------------------------

WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+|TRUNCATE|ALTER\s+TABLE|CREATE\s+TABLE)\b", re.I)


def test_no_code_path_writes_to_the_warehouse():
    for f in PKG.glob("*.py"):
        src = f.read_text()
        assert not WRITE.search(src), f"{f.name} contains write SQL"
        for banned in ("open_warehouse", "PostgresWarehouse", "SqliteWarehouse", ".transaction(", "--write", "write=True",
                       "web_research_submission (", "ingest("):
            assert banned not in src, f"{f.name} uses {banned}"
    for f in (PKG / "pipeline.py", PKG / "score.py", PKG / "gateway.py"):
        assert "psycopg" not in f.read_text() and "neon_sql" not in f.read_text(), f"{f.name} has database access"


class ReadOnlyReader:
    """Answers the export's queries from fixtures; fails on anything but a SELECT."""
    def __init__(self):
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)
        assert sql.lstrip().upper().startswith("SELECT"), sql
        if "DISTINCT release_tag FROM golden_facility" in sql:
            return [{"release_tag": "v1"}]
        if "FROM facility" in sql and "status, merged_into" in sql:
            return [{"facility_id": f"IC-{i:05d}", "status": "merged" if i == 3 else "active",
                     "merged_into": "IC-00004" if i == 3 else None} for i in range(400)]
        if "FROM web_research_submission" in sql:
            vs = ["in_scope", "not_found", "not_ic", "closed", "duplicate"]
            out = []
            for i in range(300):
                v = vs[i % 5]
                verdict = {"status": v, "reason": "because", "source_refs": ["s1", "s2"]}
                if v == "duplicate":
                    verdict["duplicate_of"] = "IC-00003"
                out.append({"facility_id": f"IC-{i:05d}", "submitted_at": "t", "status": "ingested",
                            "payload": json.dumps({"verdict": verdict, "sources": [
                                {"source_ref": "s1", "url": "https://a.com", "kind": "company_site"},
                                {"source_ref": "s2", "url": "https://b.com", "kind": "news"}],
                                "assertions": [{"field": "phone", "value": "9705222464", "source_ref": "s1", "quote": "970-522-2464"}]}),
                            "report": json.dumps({"exclusion": v if v in B.REMOVALS else None,
                                                  "duplicate_of": "IC-00003" if v == "duplicate" else None, "rejected": []})})
            return out
        if "v_assertions_resolved" in sql and "adl_validated" in sql:
            return [{"facility_id": f"IC-{i:05d}"} for i in range(250, 400, 2)]
        if "FROM fact_assertions" in sql:
            ids = re.findall(r"'(IC-\d+)'", sql)
            return [{"facility_id": f, "source_id": "fl_dbpr", "release_tag": "v1", "source_class": "A",
                     "retrieved_date": "2026-01-01", "row_hash": "h", "basis": "none", "site_visit": 0, "confidence": 0,
                     "asserted_at": "", "field": "name", "value": f"Plant {f}"} for f in ids]
        if "FROM golden_facility" in sql:
            return [{"facility_id": "IC-00004", "name": "Plant IC-00004", "state": "CO", "city": "Sterling"}]
        raise AssertionError(sql)


def test_export_reads_only_and_hashes_what_it_wrote(tmp_path):
    r = ReadOnlyReader()
    m = B.export(r, tmp_path)
    assert m["database_writes"] == 0 and m["reference_facilities"] == 300
    assert B.verify(tmp_path, m["benchmark_sha256"])["benchmark_sha256"] == m["benchmark_sha256"]
    labels = json.loads((tmp_path / "labels.json").read_text())
    inputs = json.loads((tmp_path / "inputs.json").read_text())
    assert "IC-00003" in inputs["active"]                # merged since: it was active when researched
    dups = [lab for lab in labels["facilities"].values() if lab["verdict"] == "duplicate"]
    assert dups and all(lab["duplicate_survivor"] == "IC-00004" for lab in dups)   # IC-00003 was merged into it
    assert not any("verdict" in f or "findings" in f for f in inputs["facilities"].values())
    (tmp_path / "labels.json").write_text("{}")
    with pytest.raises(RuntimeError):
        B.verify(tmp_path)


# --- an offline pass, end to end ----------------------------------------------------------------

SITE = {
    "https://acmetruss.com": {"title": "Acme Truss", "text": "Acme Truss builds roof trusses. Contact us.",
                              "links": [{"url": "https://acmetruss.com/contact", "text": "Contact"},
                                        {"url": "https://acmetruss.com/blog/1", "text": "News"}]},
    "https://acmetruss.com/contact": {"title": "Contact", "links": [],
                                      "text": "Acme Truss plant, 626 South 11th Avenue, Sterling, CO 80751. Phone (970) 522-2464."},
}


def test_offline_pass_scores_end_to_end(tmp_path, monkeypatch):
    bench = tmp_path / "bench"; bench.mkdir()
    inputs = {"release_tag": "v1", "active": ["IC-00001", "IC-00002"], "golden_index": [],
              "facilities": {"IC-00001": {"facility_id": "IC-00001", "name": "Acme Truss", "city": "Sterling",
                                          "state": "CO", "website": "acmetruss.com"},
                             "IC-00002": {"facility_id": "IC-00002", "name": "Zed Panels", "city": "Nowhere", "state": "NE"}}}
    labels = {"anchors": ["IC-00002"], "facilities": {
        "IC-00001": {"verdict": "in_scope", "findings": [
            {"field": "phone", "value": "9705222464", "quote": "Phone (970) 522-2464", "url": "https://acmetruss.com/contact"},
            {"field": "zip", "value": "80751", "quote": "CO 80751", "url": "https://acmetruss.com/contact"}]},
        "IC-00002": {"verdict": None, "findings": []}}}
    (bench / "inputs.json").write_text(json.dumps(inputs)); (bench / "labels.json").write_text(json.dumps(labels))
    si, sl = B._sha(bench / "inputs.json"), B._sha(bench / "labels.json")
    (bench / "manifest.json").write_text(json.dumps({"splits": {"smoke": ["IC-00001", "IC-00002"]}, "inputs_sha256": si,
                                                     "labels_sha256": sl, "benchmark_sha256": B.benchmark_hash(si, sl)}))
    pipe_bench = tmp_path / "pipe"; pipe_bench.mkdir()      # what the pipeline step gets: no labels
    for f in ("inputs.json", "manifest.json"):
        (pipe_bench / f).write_text((bench / f).read_text())

    def fake_fetch(self, url):
        if url in SITE:
            return dict(SITE[url], url=url, final_url=url, fetched_at="2026-09-26T00:00:00")
        return {"url": url, "error": "HTTPError: 404", "fetched_at": "2026-09-26T00:00:00"}
    monkeypatch.setattr(P.Fetcher, "_fetch", fake_fetch)
    calls = []

    def fake_chat(payload, **kw):
        calls.append(payload)
        prompt = payload["messages"][0]["content"]
        assert "9705222464" not in prompt or "PASSAGES" in prompt      # never a reference answer
        if payload.get("tools"):
            return {"choices": [{"message": {"content": json.dumps({"results": []}),
                                             "provider_metadata": {"gateway": {"gatewayToolCalls": {"tako_search": 1}}}}}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.00001}}
        elif "Acme" in prompt:
            pid = re.search(r"\[(P\d+)\] \(https://acmetruss.com/contact\)", prompt).group(1)
            body = {"verdict": {"status": "in_scope", "reason": "Acme Truss builds roof trusses in Sterling.",
                                "evidence": [{"passage": pid, "quote": "Acme Truss plant, 626 South 11th Avenue"}]},
                    "fields": {"phone": {"value": "(970) 522-2464", "passage": pid, "quote": "Phone (970) 522-2464"},
                               "address": {"value": "626 South 11th Avenue", "passage": pid, "quote": "626 South 11th Avenue, Sterling"}}}
        else:
            body = {"verdict": {"status": "closed", "reason": "made up", "evidence": []}}
        return {"choices": [{"message": {"content": json.dumps(body)}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "cost": 0.00001}}
    monkeypatch.setattr(gw, "chat", fake_chat)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"data": [{"id": m, "pricing": {"input": "0.0000001", "output": "0.0000002"}}
                                            for m in ("alibaba/qwen3.7-flash", "deepseek/deepseek-v4-flash-0731",
                                                      "google/gemini-3.1-flash-lite")]}))
    out = tmp_path / "pass"
    summary = P.run(pipe_bench, "smoke", P.DEFAULT_CONFIG, out, tmp_path / "cache", 0.10, catalog=str(catalog))
    assert summary["facilities_done"] == 2 and summary["cost"]["searches"] == 1   # only the unanchored plant searched
    assert summary["cost"]["list_usd"] <= 0.10
    audit = (out / "audit.md").read_text()                     # the per-facility audit, pipeline-side only
    assert "IC-00001" in audit and "Phone (970) 522-2464" in audit and "9705222464" not in audit.split("## Facilities")[0]
    subs = [json.loads(line) for line in (out / "submissions.jsonl").read_text().splitlines()]
    assert subs[1]["verdict"]["status"] == "not_found"          # an uncited removal never survives
    for s in subs:                                               # every submission passes the contract offline
        planned = P.contract(s, s["facility_id"], set(inputs["active"]))
        assert not [r for r in planned["rejected"] if r["item"].startswith("assertions")]
    sc = S.score(out, bench, S.Adjudicator(None, 0, None, None))
    assert sc["metrics"]["S1"] == 0 and sc["metrics"]["S3"] == 0 and sc["metrics"]["S2"] == 0
    assert sc["metrics"]["V1"] == 1.0 and sc["metrics"]["F1"] == 1.0 and sc["metrics"]["F2"] == 1.0   # the ZIP came from regex
    assert sc["metrics"]["F3_novel"] >= 1                         # the address the reference did not assert
    # a second pass re-uses the cached search and pages: no search is paid twice
    calls.clear()
    again = P.run(pipe_bench, "smoke", P.DEFAULT_CONFIG, tmp_path / "pass2", tmp_path / "cache", 0.10, catalog=str(catalog))
    assert again["cost"]["searches"] == 0 and again["cost"]["cached_searches"] == 1
    assert not any(c.get("tools") for c in calls)


def test_a_search_the_gateway_did_not_run_is_retried_then_refused_and_never_cached(tmp_path, monkeypatch):
    models = []

    def no_tool(payload, **kw):                     # smoke pass 1: the model answered without searching
        models.append(payload["model"])
        return {"choices": [{"message": {"content": '{"results": [{"url": "https://www.example.com"}]}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.000001}}
    monkeypatch.setattr(gw, "chat", no_tool)
    prices = {m: PRICES["cheap/model"] for m in ("alibaba/qwen3.7-flash", "google/gemini-3.1-flash-lite")}
    meter = gw.Meter(0.10, prices)
    rec = {"facility_id": "IC-1", "name": "Acme", "city": "X", "state": "CO"}
    with pytest.raises(P.SearchNotRun):
        P.search(rec, P.DEFAULT_CONFIG, tmp_path, meter, tmp_path)
    assert models == ["alibaba/qwen3.7-flash", "google/gemini-3.1-flash-lite"]
    assert not list((tmp_path / "search").glob("*.json"))
    assert meter.searches == 2                      # still counted at list price, conservatively


def test_search_results_survive_a_reply_cut_off_at_max_tokens():
    cut = '```json\n{"results": [{"url": "https://a.com/x", "title": "A"}, {"url": "https://b.com", "title": "B"}, {"url": "https://c.co'
    assert [r["url"] for r in P.parse_results(cut)] == ["https://a.com/x", "https://b.com"]
    whole = '{"results": [{"url": "https://a.com/x", "title": "A"}, {"url": "ftp://no"}]}'
    assert P.parse_results(whole) == [{"url": "https://a.com/x", "title": "A"}]


def test_website_from_an_anchored_company_page_and_only_same_domain_regex_emails():
    rec = {"facility_id": "IC-1", "name": "Acme Truss", "city": "Sterling", "state": "CO"}
    pages = [{"url": "https://acmetruss.com/contact", "final_url": "https://acmetruss.com/contact", "title": "Acme Truss",
              "fetched_at": "2026-09-26", "text": "Acme Truss, Sterling CO. Write to sales@acmetruss.com"},
             {"url": "https://news.example.org/a", "title": "News", "fetched_at": "2026-09-26",
              "text": "Acme Truss in Sterling expands. circulation@paper.org"}]
    regex = P.regex_candidates(pages, rec)
    regex["email"] = regex["email"][1:]                   # only the newspaper's address is left
    cfg = P.merge(P.DEFAULT_CONFIG, {"regex": {"email_same_domain": True}, "extract": {"website_from_site": True}})
    payload, _ = P.build_submission(rec, {}, [], pages, regex, cfg, {"IC-1"}, [], "acmetruss.com")
    got = {a["field"]: a["value"] for a in payload["assertions"]}
    assert got == {"website": "https://acmetruss.com"}
    assert not P.contract(payload, "IC-1", {"IC-1"})["rejected"][:1] or all(
        not r["item"].startswith("assertions") for r in P.contract(payload, "IC-1", {"IC-1"})["rejected"])


@pytest.mark.parametrize("field,value,quote,expect_value,ok", [
    ("website", "www.acme.com", "Acme", "https://www.acme.com", True),
    ("state", "Oregon", "Eugene, Oregon 97402", "OR", True),
    ("state", "OR", "Eugene 97402", "OR", False),                       # the quote does not state it
    ("capability_leaf", "Roof Trusses", "roof trusses", "Roof Trusses", False),   # not a taxonomy leaf
    ("email", "sales at acme", "sales at acme", "sales at acme", False),
])
def test_prevalidate_repairs_what_is_mechanical_and_refuses_the_rest(field, value, quote, expect_value, ok):
    got, why = P.prevalidate(field, value, quote, "https://www.acme.com/contact")
    assert got == expect_value and (why is None) is ok


def test_address_guard_holds_in_scope_on_another_street_address():
    rec = {"facility_id": "IC-1", "name": "Jensen Precast", "address": "3840 N Bruce St", "city": "North Las Vegas", "state": "NV"}
    pages = [{"url": "https://mapquest.com/j", "text": "Jensen Precast 3853 Losee Rd North Las Vegas NV", "fetched_at": "2026-09-26"}]
    psg = [{"id": "P1", "url": pages[0]["url"], "text": pages[0]["text"]}]
    answer = {"verdict": {"status": "in_scope", "reason": "Jensen Precast makes precast at 3853 Losee Rd.",
                          "evidence": [{"passage": "P1", "quote": "Jensen Precast 3853 Losee Rd"}]}}
    cfg = P.merge(P.DEFAULT_CONFIG, {"policy": {"address_guard": True}})
    payload, trace = P.build_submission(rec, answer, psg, pages, {}, cfg, {"IC-1"}, [], "")
    assert payload["verdict"]["status"] == "not_found" and trace["downgraded"]["from"] == "in_scope"
    rec["address"] = "3853 Losee Rd"                      # the same address: kept
    payload, _ = P.build_submission(rec, answer, psg, pages, {}, cfg, {"IC-1"}, [], "")
    assert payload["verdict"]["status"] == "in_scope"


def test_same_plant_by_phone_or_street():
    a = {"phone": "(623) 386-4495", "address": "231 N. Apache Rd", "city": "Buckeye"}
    assert S.same_plant(a, {"phone": "6233864495", "address": "201 N Apache Rd", "city": "BUCKEYE"})
    assert S.same_plant({"address": "3373 Busch Dr. SW", "city": "Grandville"}, {"address": "3373 Busch Dr SW", "city": "GRANDVILLE"})
    assert not S.same_plant({"address": "3373 Busch Dr SW", "city": "Grandville"}, {"address": "3373 Busch Dr SW", "city": "Wyoming"})


def test_second_look_adds_evidence_from_another_page_so_the_ingest_rule_can_pass(tmp_path, monkeypatch):
    rec = {"facility_id": "IC-1", "name": "Amcor Precast", "city": "Idaho Falls", "state": "ID"}
    pages = [{"url": "https://mapquest.com/a", "text": "Amcor Precast Closed. 2240 S Yellowstone Hwy", "fetched_at": "2026-09-26"},
             {"url": "https://news.example.com/b", "text": "Amcor Precast shut its Idaho Falls plant in 2019.", "fetched_at": "2026-09-26"}]
    psg = [{"id": "P1", "url": pages[0]["url"], "text": pages[0]["text"]}, {"id": "P2", "url": pages[1]["url"], "text": pages[1]["text"]}]
    answer = {"verdict": {"status": "closed", "reason": "MapQuest lists Amcor Precast as closed.",
                          "evidence": [{"passage": "P1", "quote": "Amcor Precast Closed"}]}}
    sent = []

    def fake_chat(payload, **kw):
        sent.append(payload["messages"][0]["content"])
        return {"choices": [{"message": {"content": json.dumps({"supports": True, "why": "news says shut",
                "evidence": [{"passage": "P2", "quote": "shut its Idaho Falls plant in 2019"}]})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.000001}}
    monkeypatch.setattr(gw, "chat", fake_chat)
    cfg = P.merge(P.DEFAULT_CONFIG, {"policy": {"removal_second_look": True}})
    meter = gw.Meter(0.10, {cfg["judge"]["model"]: PRICES["cheap/model"]})
    out = P.second_look(rec, answer, psg, pages, cfg, meter, tmp_path)
    assert "P1" not in sent[0] and "P2" in sent[0]           # only the other page's passages are shown
    payload, trace = P.build_submission(rec, out, psg, pages, {}, cfg, {"IC-1"}, [], "")
    assert payload["verdict"]["status"] == "closed" and not trace.get("downgraded")
    assert not [r for r in P.contract(payload, "IC-1", {"IC-1"})["rejected"] if r["item"] == "verdict"]


def test_sibling_sites_come_from_other_rows_of_the_same_company():
    rec = {"facility_id": "IC-1", "name": "CHAMPION HOME BUILDERS #261", "state": "FL"}
    index = [{"facility_id": "IC-2", "name": "Champion Home Builders - Lake City", "state": "FL", "website": "https://www.championhomes.com/x"},
             {"facility_id": "IC-3", "name": "Champion Homes", "state": "OR", "website": "www.yelp.com/biz/champion"},
             {"facility_id": "IC-1", "name": "Champion", "state": "FL", "website": "https://self.example.com"},
             {"facility_id": "IC-4", "name": "Clayton Homes", "state": "FL", "website": "https://claytonhomes.com"}]
    assert P.sibling_sites(rec, index) == ["https://championhomes.com"]


@pytest.mark.parametrize("url,kind", [
    ("https://www2.deq.idaho.gov/admin/LEIA/api/document/download/9137", "other"),      # b-v2c: an air permit
    ("https://www.osha.gov/ords/imis/establishment.inspection_detail?id=1", "other"),
    ("https://psc.mo.gov/CMSInternetData/ManufacturedHousing/Manufacturer/ACTIVE%20MOD.pdf", "government_registry"),
    ("https://sos.state.xx.us/business/entity/123", "filing"),
])
def test_only_listing_and_licensing_government_pages_are_registry_grade(url, kind):
    assert P.source_kind(url, {"name": "Acme"}, "") == kind


def test_a_glulam_plant_is_not_removed_on_one_government_permit():
    rec = {"facility_id": "IC-48445", "name": "HOMEDALE ENGINEERED WOOD PLANT", "city": "Homedale", "state": "ID",
           "capability_group": "Other", "capability_leaf": "Wood Structural Components (Trusses, etc.)"}
    url = "https://www2.deq.idaho.gov/admin/LEIA/api/document/download/9137"
    pages = [{"url": url, "text": "Facility Location 4318 Pioneer Road Homedale. laminated beams and decking", "fetched_at": "2026-09-26"}]
    psg = [{"id": "P1", "url": url, "text": pages[0]["text"]}]
    answer = {"verdict": {"status": "not_ic", "reason": "Glulam is not off-site construction.",
                          "evidence": [{"passage": "P1", "quote": "laminated beams and decking"}]}}
    payload, trace = P.build_submission(rec, answer, psg, pages, {}, P.DEFAULT_CONFIG, {"IC-48445"}, [], "")
    assert payload["verdict"]["status"] == "not_found"
    assert "glulam" in P.JUDGE_PROMPT


def test_address_guard_also_holds_a_removal_about_another_address():
    rec = {"facility_id": "IC-76000", "name": "BROCCA MANUFACTURING CO INC", "address": "200 Brocca Dr", "city": "Kingston", "state": "PA"}
    url1, url2 = "https://a.example.com/x", "https://b.example.com/y"
    pages = [{"url": url1, "text": "Brocca Garages Inc., 4 Curran St, Pittston PA builds garages", "fetched_at": "2026-09-26"},
             {"url": url2, "text": "Brocca Garages at 4 Curran St sells sheds", "fetched_at": "2026-09-26"}]
    psg = [{"id": "P1", "url": url1, "text": pages[0]["text"]}, {"id": "P2", "url": url2, "text": pages[1]["text"]}]
    answer = {"verdict": {"status": "not_ic", "reason": "Brocca Garages at 4 Curran St builds garages, a different address.",
                          "evidence": [{"passage": "P1", "quote": "Brocca Garages Inc., 4 Curran St"}, {"passage": "P2", "quote": "Brocca Garages at 4 Curran St"}]}}
    cfg = P.merge(P.DEFAULT_CONFIG, {"policy": {"address_guard": True}})
    payload, trace = P.build_submission(rec, answer, psg, pages, {}, cfg, {"IC-76000"}, [], "")
    assert payload["verdict"]["status"] == "not_found" and trace["downgraded"]["from"] == "not_ic"


@pytest.mark.parametrize("second,expect", [("unsure", "not_found"), ("in_scope", "not_found"), ("not_ic", "not_ic")])
def test_not_ic_stands_only_when_a_second_model_agrees(tmp_path, monkeypatch, second, expect):
    rec = {"facility_id": "IC-94090", "name": "BiltWise Structures", "city": "Greenwood", "state": "SC"}
    url1, url2 = "https://biltwisestructures.com/", "https://biltwisestructures.com/?page_id=4828"
    pages = [{"url": url1, "text": "BiltWise Structures Corporate Offices (By Appointment Only) Greenwood SC", "fetched_at": "2026-09-26"},
             {"url": url2, "text": "BiltWise Structures Greenwood SC corporate administration", "fetched_at": "2026-09-26"}]
    psg = [{"id": "P1", "url": url1, "text": pages[0]["text"]}, {"id": "P2", "url": url2, "text": pages[1]["text"]}]
    answer = {"verdict": {"status": "not_ic", "reason": "The Greenwood address is corporate offices only.",
                          "evidence": [{"passage": "P1", "quote": "Corporate Offices (By Appointment Only)"},
                                       {"passage": "P2", "quote": "corporate administration"}]}}
    models = []

    def fake_chat(payload, **kw):
        models.append(payload["model"])
        return {"choices": [{"message": {"content": json.dumps({"answer": second, "why": "w"})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.000001}}
    monkeypatch.setattr(gw, "chat", fake_chat)
    cfg = P.merge(P.DEFAULT_CONFIG, {"policy": {"not_ic_second_opinion": "openai/gpt-oss-120b", "capability_removal_guard": False}})
    meter = gw.Meter(0.10, {"openai/gpt-oss-120b": PRICES["cheap/model"]})
    out = P.second_opinion(rec, answer, psg, cfg, meter, tmp_path)
    payload, trace = P.build_submission(rec, out, psg, pages, {}, cfg, {"IC-94090"}, [], "biltwisestructures.com")
    assert models == ["openai/gpt-oss-120b"] and payload["verdict"]["status"] == expect
    assert trace["second_opinion"]["held"] is (expect == "not_found")


def test_scope_names_the_products_c_v6_wrongly_removed():
    for words in ("insulated sandwich building panels", "log and\ntimber homes", "modular steel buildings"):
        assert words in P.JUDGE_PROMPT
    assert "lists this address as an office" in P.STRICT_SITE and "model village, office" not in P.STRICT_SITE
