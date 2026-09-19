"""Best-effort dashboard telemetry. Never lets reporting fail a data run.

Reports contain aggregate metrics only, keyed by GitHub run AND attempt. They live
in the release database even when refinement uses an isolated Neon branch.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

DDL = """CREATE TABLE IF NOT EXISTS pipeline_run_events (
 run_id TEXT NOT NULL, attempt INTEGER NOT NULL, stage TEXT NOT NULL,
 state TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), data JSONB NOT NULL,
 PRIMARY KEY(run_id,attempt,stage))"""
SAFE = {"phase", "batches_done", "batches_total", "secs_per_batch", "eta_s", "rows_labelled",
        "input_tokens", "output_tokens", "model", "published_count", "release_tag", "elapsed_s",
        "attempted", "located", "from_cache", "deferred", "requested", "inserted", "already_present",
        "conflicts", "golden_rows", "written", "halted", "stored", "billed_lookups",
        "billable_if_allowance_spent_usd", "usage", "target", "results", "cost", "status"}


def connection(reporting=True):
    import psycopg
    url = (os.environ.get("PIPELINE_REPORT_DATABASE_URL") if reporting else None) or os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No reporting database")
    return psycopg.connect(url, connect_timeout=3, options="-c statement_timeout=5000 -c lock_timeout=2000")


def emit(stage: str, state: str, data: dict):
    if not os.environ.get("GITHUB_RUN_ID"):
        return
    try:
        safe = {k: v for k, v in data.items() if k in SAFE}
        safe["target"] = os.environ.get("PIPELINE_TARGET", "main")
        with connection() as conn:
            conn.execute(DDL)
            conn.execute("""INSERT INTO pipeline_run_events(run_id,attempt,stage,state,data)
              VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT(run_id,attempt,stage)
              DO UPDATE SET state=excluded.state,data=excluded.data,updated_at=now()""",
              (os.environ["GITHUB_RUN_ID"], int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")), stage, state, json.dumps(safe)))
    except Exception:
        print("::warning::Dashboard telemetry unavailable; the data run continues.")


def fingerprint(rows):
    result = {}
    for row in rows:
        # Store hashes, never employee notes or factory field values, in the baseline artifact.
        key = row.pop("facility_key")
        fields = {k: v for k, v in row.items() if k + "__source" in row}
        result[key] = {k: hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest() for k, v in fields.items()}
    return result


def snapshot():
    from psycopg.rows import dict_row
    with connection(False) as conn:
        # Consistent baseline across both tables, without blocking employee saves.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = conn.cursor(row_factory=dict_row).execute("SELECT * FROM golden_facility").fetchall()
        count = conn.execute("SELECT count(*) FROM fact_assertions").fetchone()[0]
    return {"facilities": fingerprint(rows), "assertions": count}


def changes(before, after):
    a, b = before["facilities"], after["facilities"]
    common = a.keys() & b.keys()
    return {"factories_added": len(b.keys() - a.keys()), "factories_removed": len(a.keys() - b.keys()),
            "factories_updated": sum(a[k] != b[k] for k in common),
            "golden_fields_changed": sum(a[k].get(f) != v for k in common for f, v in b[k].items()),
            "assertion_rows_added": after["assertions"] - before["assertions"], "factories_total": len(b)}


def estimate_cost(model, usage):
    """Snapshot current per-token list rates. This is explicitly NOT a provider invoice."""
    report = {"model": model, "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
              "model_usd": None, "basis": "estimate", "scope": "Model tokens only; excludes search, geocoding, storage and runner charges."}
    if not model or any(usage.get(k) is None for k in ("input_tokens", "output_tokens")):
        return report
    try:
        with urllib.request.urlopen("https://ai-gateway.vercel.sh/v1/models", timeout=5) as r:
            models = json.load(r)["data"]
        rate = next(m["pricing"] for m in models if m["id"] == model)
        # Aggregate usage cannot accurately price tiered contexts; do not invent a total.
        if rate.get("input_tiers") or rate.get("output_tiers"):
            return report
        report.update(model_usd=round(float(rate["input"]) * usage["input_tokens"] + float(rate["output"]) * usage["output_tokens"], 6),
                      input_usd_per_token=rate["input"], output_usd_per_token=rate["output"],
                      pricing_source="https://ai-gateway.vercel.sh/v1/models")
    except Exception:
        pass
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["before", "after"])
    ap.add_argument("kind", choices=["ingestion", "refinement"])
    ap.add_argument("--stage", default="summary")
    ap.add_argument("--state", default="success")
    ap.add_argument("--out", type=Path, default=Path("build"))
    args = ap.parse_args()
    baseline = args.out / ("plan.baseline.json" if args.kind == "refinement" else "run-baseline.json")
    try:
        if args.action == "before":
            # Each stage reports its own start. Only plan/ingestion capture a baseline.
            emit(args.stage, "running", {})
            if args.stage in ("plan", "summary"):
                baseline.parent.mkdir(parents=True, exist_ok=True)
                baseline.write_text(json.dumps(snapshot()))
            return
        report = {}
        if args.kind == "refinement":
            p = args.out / f"{args.stage}.json"
            if p.exists():
                report = json.loads(p.read_text())
            if args.stage == "locate":
                report["cost"] = estimate_cost(report.get("model"), report.get("usage", {}))
        else:
            # Record only this execution, not whichever historical record sorts last.
            paths = [p for p in Path("run_records").glob("*.json") if p.stat().st_mtime >= baseline.stat().st_mtime] if baseline.exists() else []
            if paths:
                record = json.loads(max(paths, key=lambda p:p.stat().st_mtime).read_text())
                cls = record.get("layers", {}).get("3_classify", {})
                report = {"conflicts": record.get("layers", {}).get("5b_golden", {}).get("conflicts"),
                          "cost": estimate_cost(cls.get("model"), cls)}
        if baseline.exists() and (args.kind == "ingestion" or args.stage == "promote"):
            report["results"] = changes(json.loads(baseline.read_text()), snapshot())
        emit(args.stage, args.state, report)
    except Exception:
        print("::warning::Run metrics incomplete; inspect the workflow for results.")


if __name__ == "__main__":
    main()
