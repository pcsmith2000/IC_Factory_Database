"""Production web research in growing batches (docs/web-research-production-loop.md).

The research itself is the evaluated pipeline (pipeline/research_eval/pipeline.py, configuration
configs/production.json). This module adds what production needs around it:

    select   the next batch: active golden facilities no submission has covered yet, in a seeded order.
             Read-only session. Writes a folder shaped like the evaluation benchmark's inputs.
    health   the batch's gates, from the pass folders (one per shard) and a judge sample of new facts:
             errors, quote re-check, contract refusals, cost, removal and duplicate shares, ADL-validated
             plants untouched, and the judge's precision on a random sample. Decides grow, hold or stop.
    submit   inserts the batch's submissions into web_research_submission as pending, tagged with the
             batch's run_id; the web-research-ingest workflow validates and writes them. Only after
             health passes. It writes nothing else.

    python -m pipeline.web_research.production select --run-id wr-prod-001 --size 25 --out batch
    python -m pipeline.web_research.production health --batch batch --pass shard-0 --pass shard-1 --out health.json
    python -m pipeline.web_research.production submit --run-id wr-prod-001 --pass shard-0 --health health.json
"""
from __future__ import annotations
import argparse, hashlib, json, os, random, re, sys
from datetime import datetime, timezone
from pathlib import Path

INPUT_FIELDS = ("name", "legal_name", "address", "city", "state", "zip", "lat_lon", "website", "phone", "email",
                "naics", "product_type", "operating_status", "capability_group", "capability_leaf", "material",
                "adl_validated")
RUN_ID = re.compile(r"^wr-prod-\d{3}$")
LITERAL = ("name", "address", "city", "state", "zip", "phone", "email", "website")

# The gates a batch must pass before the next one grows (docs/web-research-production-loop.md).
GATES = {
    "error_share": ("<=", 0.05),          # facilities that failed outright
    "quote_failures": ("==", 0),          # quotes not found verbatim on the fetched page (re-checked)
    "refused_share": ("<=", 0.02),        # findings ingest would refuse
    "list_usd_per_facility": ("<=", 0.012),
    "closed_share": ("<=", 0.10),         # removals are rare; a spike means something is wrong
    "not_ic_applied": ("==", 0),          # not_ic always goes to review in production
    "validated_removed": ("==", 0),       # an ADL-validated plant is never removed
    # wr-prod-001: 5 of 25 were duplicates, all real (same address or phone as an active record). The backlog
    # holds more duplicates than the benchmark; only a spike beyond this suggests the matcher is overreaching.
    "duplicate_share": ("<=", 0.30),
    "judge_precision": (">=", 0.85),      # new literal facts the judge found correct, on a random sample
}


def _check(op, v, th):
    return v == th if op == "==" else v <= th if op == "<=" else v >= th


# --- select (read-only) -------------------------------------------------------------------------

def select_sql(size: int, seed: int) -> str:
    cols = ", ".join(f"g.{f}" for f in INPUT_FIELDS)
    return f"""
    SELECT g.facility_key AS facility_id, {cols}
    FROM golden_facility g JOIN facility f ON f.facility_id = g.facility_key AND f.status = 'active'
    WHERE NOT EXISTS (SELECT 1 FROM web_research_submission s WHERE s.facility_id = g.facility_key)
    ORDER BY md5(g.facility_key || '{int(seed)}'), g.facility_key
    LIMIT {int(size)}"""


def select(reader, run_id: str, size: int, seed: int, out: Path) -> dict:
    from ..research_eval.benchmark import benchmark_hash, _sha
    if not RUN_ID.match(run_id):
        raise ValueError("run_id must look like wr-prod-001")
    rows = reader.query(select_sql(size, seed))
    facilities = {r["facility_id"]: {"facility_id": r["facility_id"],
                                     **{f: r[f] for f in INPUT_FIELDS if r.get(f) not in (None, "")}} for r in rows}
    active = sorted(r["facility_id"] for r in reader.query("SELECT facility_id FROM facility WHERE status = 'active'"))
    golden_index = reader.query("SELECT facility_key AS facility_id, name, address, city, state, zip, phone, website "
                                "FROM golden_facility ORDER BY facility_key")
    out.mkdir(parents=True, exist_ok=True)
    (out / "inputs.json").write_text(json.dumps({"facilities": facilities, "active": active,
                                                 "golden_index": golden_index}, indent=1, sort_keys=True, default=str))
    si = _sha(out / "inputs.json")
    manifest = {"run_id": run_id, "selected_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
                "requested": size, "selected": len(facilities), "splits": {"batch": sorted(facilities)},
                "inputs_sha256": si, "labels_sha256": "", "benchmark_sha256": benchmark_hash(si, ""),
                "database_writes": 0}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


# --- health -------------------------------------------------------------------------------------

def health(batch: Path, pass_dirs: list[Path], adjudicator=None, sample: int = 20, seed: int = 20260926) -> dict:
    """The batch's gates. Reads only the pass folders and the batch inputs; the judge sees new facts
    with the page text they came from."""
    from ..research_eval.score import load_pass, merge_summaries
    inputs = json.loads((batch / "inputs.json").read_text())
    active = set(inputs["active"])
    summary = merge_summaries([json.loads((d / "summary.json").read_text()) for d in pass_dirs])
    got, reviews, traces = {}, [], {}
    for d in pass_dirs:
        got.update(load_pass(d, active))
        for t in (d / "facilities").glob("*/trace.json"):
            tr = json.loads(t.read_text())
            traces[t.parent.name] = tr
            if tr.get("review"):
                reviews.append({"facility_id": t.parent.name, **tr["review"]})
    n = len(got) or 1
    findings = [(f, x) for f, g in got.items() for x in g["findings"]]
    quote_fail = sum(1 for f, x in findings
                     if re.sub(r"\s+", " ", x["quote"]).strip()
                     not in re.sub(r"\s+", " ", got[f]["pages"].get(x["url"], "").replace(" ", " ")))
    refused = sum(1 for _, x in findings if not x["accepted"])
    validated = {f for f, r in inputs["facilities"].items() if str(r.get("adl_validated") or "").lower() in ("1", "true", "yes", "y")}
    verdicts = {f: g["verdict"] for f, g in got.items()}
    novel = [(f, x) for f, x in findings if x["accepted"] and x["field"] in LITERAL
             and not inputs["facilities"].get(f, {}).get(x["field"])]
    rng = random.Random(seed)
    rng.shuffle(novel)
    judged = []
    if adjudicator is not None:
        for f, x in novel[:sample]:
            judged.append(adjudicator.novel(f, inputs["facilities"].get(f, {}), x["field"], x, got[f]["pages"].get(x["url"], "")))
    ok = [c for c in judged if c.get("answer") in ("correct", "incorrect")]
    metrics = {
        "facilities": len(got), "planned": summary["facilities_planned"], "errors": len(summary["errors"]),
        "error_share": round(len(summary["errors"]) / max(summary["facilities_planned"], 1), 4),
        "quote_failures": quote_fail, "findings": len(findings),
        "refused_share": round(refused / len(findings), 4) if findings else 0.0,
        "list_usd_per_facility": round(summary["cost"]["list_usd"] / n, 6),
        "billed_usd": summary["cost"]["billed_usd"], "list_usd": summary["cost"]["list_usd"],
        "closed_share": round(sum(v == "closed" for v in verdicts.values()) / n, 4),
        "not_ic_applied": sum(v == "not_ic" for v in verdicts.values()),
        "validated_removed": sum(1 for f in validated if verdicts.get(f) in ("not_ic", "closed")),
        "duplicate_share": round(sum(v == "duplicate" for v in verdicts.values()) / n, 4),
        "in_scope_share": round(sum(v == "in_scope" for v in verdicts.values()) / n, 4),
        "review_items": len(reviews), "new_literal_facts": len(novel),
        "literal_per_facility": round(sum(1 for _, x in findings if x["accepted"] and x["field"] in LITERAL) / n, 3),
        "judge_sampled": len(ok),
        "judge_precision": round(sum(c["answer"] == "correct" for c in ok) / len(ok), 4) if ok else None,
    }
    gates = {k: (None if metrics.get(k) is None else _check(op, metrics[k], th)) for k, (op, th) in GATES.items()}
    failed = [k for k, v in gates.items() if v is False]
    # Too few facts for the judge to sample is not a failure of the batch.
    decision = "stop" if (metrics["validated_removed"] or metrics["quote_failures"] or metrics["not_ic_applied"]) \
        else "grow" if not failed else "hold"
    return {"run_id": summary.get("run_id"), "metrics": metrics, "gates": gates, "failed": failed,
            "decision": decision, "judge_cases": judged, "review": reviews,
            "judge_cost": adjudicator.meter.summary() if adjudicator is not None else None}


# --- submit (the one write) ---------------------------------------------------------------------

INSERT = ("INSERT INTO web_research_submission (submission_id, facility_id, run_id, agent, submitted_at, payload) "
          "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (submission_id) DO NOTHING")


def submission_rows(run_id: str, pass_dirs: list[Path]) -> list[tuple]:
    if not RUN_ID.match(run_id):
        raise ValueError("run_id must look like wr-prod-001")
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for d in pass_dirs:
        for line in (d / "submissions.jsonl").read_text().splitlines():
            sub = json.loads(line)
            sub["run_id"] = run_id
            sub["agent"] = f"research pipeline {sub.get('agent', '')}".strip()
            rows.append((f"{run_id}:{sub['facility_id']}", sub["facility_id"], run_id, sub["agent"], now, json.dumps(sub)))
    return rows


def submit(url: str, run_id: str, pass_dirs: list[Path], health_report: dict) -> int:
    if health_report.get("decision") != "grow":
        raise RuntimeError(f"health decision is {health_report.get('decision')!r}: nothing submitted")
    rows = submission_rows(run_id, pass_dirs)
    import psycopg
    with psycopg.connect(url, connect_timeout=20) as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.executemany(INSERT, rows)
    return len(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.web_research.production")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select"); s.add_argument("--run-id", required=True); s.add_argument("--size", type=int, required=True)
    s.add_argument("--seed", type=int, default=20260927); s.add_argument("--out", default="batch")
    h = sub.add_parser("health"); h.add_argument("--batch", required=True); h.add_argument("--pass", dest="passes", action="append", required=True)
    h.add_argument("--out", default="health.json"); h.add_argument("--judge-model", default="google/gemini-3-flash")
    h.add_argument("--judge-max-usd", type=float, default=0.05); h.add_argument("--sample", type=int, default=20)
    w = sub.add_parser("submit"); w.add_argument("--run-id", required=True); w.add_argument("--pass", dest="passes", action="append", required=True)
    w.add_argument("--health", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "select":
        from ..research_eval.benchmark import PsycopgReader
        url = next((u for u in (os.environ.get("DATABASE_URL_UNPOOLED", ""), os.environ.get("DATABASE_URL", "")) if u), "")
        if not 1 <= a.size <= 2000:
            print("size must be 1..2000", file=sys.stderr); return 2
        m = select(PsycopgReader(url), a.run_id, a.size, a.seed, Path(a.out))
        print(json.dumps({k: v for k, v in m.items() if k != "splits"}, indent=1)); return 0
    if a.cmd == "health":
        from ..research_eval import gateway as gw
        from ..research_eval.score import Adjudicator
        adj = Adjudicator(a.judge_model, a.judge_max_usd, gw.load_catalog(), Path("eval-cache/adjudication")) \
            if a.judge_max_usd > 0 and os.environ.get("AI_GATEWAY_API_KEY") else None
        rep = health(Path(a.batch), [Path(p) for p in a.passes], adj, a.sample)
        Path(a.out).write_text(json.dumps(rep, indent=1, default=str))
        md = [f"## Batch health: {rep['decision'].upper()}", "", "| Gate | Value | Rule | OK |", "| --- | --- | --- | --- |"]
        for k, (op, th) in GATES.items():
            md.append(f"| {k} | {rep['metrics'].get(k)} | {op} {th} | {'✅' if rep['gates'][k] else '❌' if rep['gates'][k] is False else '–'} |")
        md += ["", f"```json\n{json.dumps(rep['metrics'])}\n```", f"Review items (not_ic proposals): {len(rep['review'])}"]
        print("\n".join(md))
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            open(os.environ["GITHUB_STEP_SUMMARY"], "a").write("\n".join(md) + "\n")
        return 0
    if a.cmd == "submit":
        rep = json.loads(Path(a.health).read_text())
        url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
        n = submit(url, a.run_id, [Path(p) for p in a.passes], rep)
        print(f"submitted {n} pending submissions for {a.run_id}"); return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
