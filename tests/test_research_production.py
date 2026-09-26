"""Production web research: batch selection (read-only), health gates, and the one write (pending submissions)."""
import json
import re
from pathlib import Path

import pytest

from pipeline.research_eval import benchmark as B
from pipeline.research_eval import pipeline as P
from pipeline.web_research import production as W


class Reader:
    def __init__(self):
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)
        assert sql.lstrip().upper().startswith("SELECT"), sql
        if "web_research_submission" in sql:
            return [{"facility_id": "IC-00001", "name": "Acme Truss", "city": "Sterling", "state": "CO",
                     "website": "acmetruss.com", "adl_validated": None, **{f: None for f in W.INPUT_FIELDS
                                                                           if f not in ("name", "city", "state", "website", "adl_validated")}}]
        if "FROM facility" in sql:
            return [{"facility_id": "IC-00001"}, {"facility_id": "IC-00002"}]
        return [{"facility_id": "IC-00002", "name": "Zed", "city": "X", "state": "NE"}]


def test_select_skips_researched_facilities_reads_only_and_is_a_valid_pipeline_input(tmp_path):
    r = Reader()
    m = W.select(r, "wr-prod-001", 25, 7, tmp_path)
    assert m["selected"] == 1 and m["splits"]["batch"] == ["IC-00001"] and m["database_writes"] == 0
    assert "NOT EXISTS (SELECT 1 FROM web_research_submission" in r.sql[0] and "LIMIT 25" in r.sql[0]
    assert B.verify(tmp_path)["benchmark_sha256"] == m["benchmark_sha256"]        # the pipeline accepts it
    with pytest.raises(ValueError):
        W.select(r, "prod-1", 25, 7, tmp_path)


def _pass(tmp_path, verdict="in_scope", adl=None, quote="Phone (970) 522-2464"):
    batch, out = tmp_path / "batch", tmp_path / "pass"
    batch.mkdir(parents=True); (out / "facilities" / "IC-00001").mkdir(parents=True)
    rec = {"facility_id": "IC-00001", "name": "Acme", "city": "Sterling", "state": "CO", **({"adl_validated": adl} if adl else {})}
    (batch / "inputs.json").write_text(json.dumps({"facilities": {"IC-00001": rec}, "active": ["IC-00001"], "golden_index": []}))
    url = "https://acmetruss.com/contact"
    sub = {"facility_id": "IC-00001", "agent": "research_eval production-v9", "run_id": "production-v9",
           "verdict": {"status": verdict, "reason": "Acme Truss builds trusses in Sterling.", "source_refs": ["s1"]},
           "sources": [{"source_ref": "s1", "url": url, "kind": "company_site", "found_by": "x", "retrieved_at": "2026-09-27"}],
           "assertions": [{"field": "phone", "value": "9705222464", "source_ref": "s1", "quote": quote, "confidence": 0.8}]}
    (out / "submissions.jsonl").write_text(json.dumps(sub) + "\n")
    (out / "facilities" / "IC-00001" / "pages.json").write_text(json.dumps([{"url": url, "text": "Acme Truss plant. Phone (970) 522-2464."}]))
    (out / "facilities" / "IC-00001" / "trace.json").write_text(json.dumps({"dropped": [], "kept": []}))
    (out / "summary.json").write_text(json.dumps({"batch": "batch[0:200]", "run_id": "1", "config": "production-v9", "benchmark_sha256": "x",
        "facilities_planned": 1, "facilities_done": 1, "runner_seconds": 20, "errors": [],
        "cost": {"max_cost_usd": 0.4, "billed_usd": 0.002, "list_usd": 0.008, "calls": 2, "searches": 1,
                 "cached_searches": 0, "responses_missing_cost": 0, "tokens": {}}}))
    return batch, out


def test_a_clean_batch_grows(tmp_path):
    batch, out = _pass(tmp_path)
    rep = W.health(batch, [out])
    assert rep["decision"] == "grow" and rep["metrics"]["quote_failures"] == 0 and rep["metrics"]["new_literal_facts"] == 1
    assert rep["gates"]["judge_precision"] is None                               # no judge offline: not a failure


def test_a_quote_not_on_the_page_or_a_removed_validated_plant_stops_the_ramp(tmp_path):
    batch, out = _pass(tmp_path, quote="Phone (970) 000-0000")
    assert W.health(batch, [out])["decision"] == "stop"
    batch, out = _pass(tmp_path / "b", verdict="closed", adl="1")
    rep = W.health(batch, [out])
    assert rep["metrics"]["validated_removed"] == 1 and rep["decision"] == "stop"


def test_submission_rows_carry_the_batch_run_id_and_submit_refuses_without_grow(tmp_path):
    _, out = _pass(tmp_path)
    rows = W.submission_rows("wr-prod-001", [out])
    assert rows[0][:3] == ("wr-prod-001:IC-00001", "IC-00001", "wr-prod-001")
    assert json.loads(rows[0][5])["run_id"] == "wr-prod-001"
    with pytest.raises(RuntimeError):
        W.submit("postgresql://unused", "wr-prod-001", [out], {"decision": "hold"})


def test_the_only_write_is_a_pending_submission():
    src = Path(W.__file__).read_text()
    writes = re.findall(r"\b(INSERT\s+INTO\s+\w+|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP|TRUNCATE|ALTER\s+TABLE)", src, re.I)
    assert writes == ["INSERT INTO web_research_submission"]
    assert "ON CONFLICT (submission_id) DO NOTHING" in W.INSERT


def test_production_config_is_the_evaluated_v9_plus_the_production_rules():
    cfg = json.loads((Path(P.__file__).parent / "configs" / "production.json").read_text())
    assert cfg["judge"]["model"] == "alibaba/qwen3.7-flash"
    assert cfg["policy"]["not_ic_to_review"] and cfg["policy"]["never_remove_validated"] and cfg["policy"]["not_ic_keyword_veto"]
