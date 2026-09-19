import json
from pipeline import run_report


def test_changes_separate_added_removed_updated_and_field_changes():
    before = {"facilities": {"a": {"name": "1", "city": "2"}, "b": {"name": "3"}}, "assertions": 10}
    after = {"facilities": {"a": {"name": "4", "city": "2"}, "c": {"name": "5"}}, "assertions": 15}
    assert run_report.changes(before, after) == {"factories_added": 1, "factories_removed": 1, "factories_updated": 1, "golden_fields_changed": 1, "assertion_rows_added": 5, "factories_total": 2}


def test_snapshot_does_not_store_employee_text():
    result = run_report.fingerprint([{"facility_key": "a", "employee_notes": "private note", "employee_notes__source": "adl_employee_feedback", "release_tag": "x"}])
    assert "private note" not in json.dumps(result)
    assert "release_tag" not in result["a"]


def test_estimated_cost_uses_per_token_rates_and_preserves_missing(monkeypatch):
    class Reply:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"data": [{"id": "test/model", "pricing": {"input": "0.000001", "output": "0.000002"}}]}).encode()
    monkeypatch.setattr(run_report.urllib.request, "urlopen", lambda *a, **kw: Reply())
    result = run_report.estimate_cost("test/model", {"input_tokens": 100, "output_tokens": 50})
    assert result["model_usd"] == 0.0002
    assert result["basis"] == "estimate"
    assert run_report.estimate_cost("test/model", {})["model_usd"] is None


def test_reporting_failure_never_raises(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setattr(run_report, "connection", lambda: (_ for _ in ()).throw(RuntimeError("secret")))
    run_report.emit("plan", "running", {})
    assert "secret" not in capsys.readouterr().out


def test_attempt_key_and_safe_metrics(monkeypatch):
    calls = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): calls.append(args)
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setattr(run_report, "connection", lambda: Conn())
    run_report.emit("locate", "running", {"attempted": 3, "assertions": [{"secret": "not telemetry"}]})
    values = calls[-1][1]
    assert values[:4] == ("123", 2, "locate", "running")
    assert "assertions" not in values[-1]
