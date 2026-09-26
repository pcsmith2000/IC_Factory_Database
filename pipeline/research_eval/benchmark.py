"""Export the frozen benchmark (design section 2), read-only.

Three files, written to --out and uploaded as one artifact; none is ever committed:

    inputs.json    what the pipeline may see: each facility's record as it was BEFORE the research
                   agent touched it (survivorship over its assertions without source web_research),
                   the active facility ids, and a golden index for the duplicate check
    labels.json    what only the scorer may see: the wr-full-1 / wr-pilot-1 reference, per facility
    manifest.json  the splits, counts, seed, and the SHA-256 of both files; the benchmark hash is
                   the SHA-256 of the two file hashes, and every pass names it

The database is opened with default_transaction_read_only=on and the session is checked before any
query runs. Locally, --neon-http reads through Neon's HTTPS endpoint in a read-only batch instead.

    python -m pipeline.research_eval.benchmark --out benchmark/
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
REFERENCE_RUNS = ("wr-full-1", "wr-pilot-1")
LITERAL = ("name", "address", "city", "state", "zip", "phone", "email", "website")
JUDGEMENT = ("capability_group", "capability_leaf", "material")
REMOVALS = ("not_ic", "closed")
REMOVAL_GRADE = ("government_registry", "filing", "certification_body")
SEED = 20260926
BATCH = 25
DEV_BATCHES = ("dev_a", "dev_b", "dev_c", "dev_d", "dev_e", "dev_f")
HOLDOUT = 100
ANCHORS = 50
# Per 25: about 30% removals and 15% duplicates, so the safety metrics have cases to count.
MIX = {"removal": 0.30, "duplicate": 0.15}
INPUT_FIELDS = ("name", "legal_name", "address", "city", "state", "zip", "lat_lon", "website", "phone", "email",
                "naics", "product_type", "operating_status", "capability_group", "capability_leaf", "material",
                "primary_capability", "sector", "existence_flag", "adl_validated")
_ID = re.compile(r"^[A-Z]+-\d+$")


# --- read-only database access ------------------------------------------------------------------

class PsycopgReader:
    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self.db = psycopg.connect(url, connect_timeout=20, row_factory=dict_row,
                                  options="-c default_transaction_read_only=on -c statement_timeout=120000")
        self.db.read_only = True
        if self.db.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] != "on":
            raise RuntimeError("database read-only protection was not enabled")

    def query(self, sql: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql).fetchall()]


class NeonHttpReader:
    """Neon's HTTPS SQL endpoint in a read-only batch (pipeline/neon_sql.py), for local runs."""
    def query(self, sql: str) -> list[dict]:
        from .. import neon_sql
        return neon_sql.run([sql], write=False)[0]


def _ids(ids) -> str:
    ids = sorted(set(ids))
    bad = [i for i in ids if not _ID.match(i)]
    if bad:
        raise ValueError(f"unexpected facility id {bad[0]!r}")
    return ",".join(f"'{i}'" for i in ids) or "''"


def _chunks(xs, n):
    xs = sorted(set(xs))
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# --- the reference ------------------------------------------------------------------------------

def _loads(v):
    return v if isinstance(v, dict) else json.loads(v or "{}")


def reference_label(payload: dict, report: dict, facilities: dict) -> dict:
    """The reference for one facility: its verdict, removal strength, duplicate target and the
    findings ingest accepted (the payload's assertions minus report.rejected)."""
    sources = {s.get("source_ref"): s for s in payload.get("sources") or [] if s.get("source_ref")}
    rejected = {r["item"] for r in report.get("rejected") or [] if isinstance(r, dict) and r.get("item")}
    verdict = payload.get("verdict") or {}
    status = verdict.get("status")
    cited = [sources[r] for r in verdict.get("source_refs") or [] if r in sources]
    findings = []
    for i, a in enumerate(payload.get("assertions") or []):
        if f"assertions[{i}]" in rejected:
            continue
        s = sources.get(a.get("source_ref")) or {}
        findings.append({"field": a.get("field"), "value": str(a.get("value") or ""), "quote": a.get("quote") or "",
                         "url": s.get("url") or "", "kind": s.get("kind") or "other"})
    removal_accepted = bool(report.get("exclusion"))
    strength = None
    if status in REMOVALS:
        strength = ("strong" if len({c.get("url") for c in cited}) >= 2 or any(c.get("kind") in REMOVAL_GRADE for c in cited)
                    else "weak")
    dup = report.get("duplicate_of") or (verdict.get("duplicate_of") if status == "duplicate" else None)
    return {"verdict": status, "verdict_reason": verdict.get("reason") or "",
            "verdict_sources": [{"url": c.get("url"), "kind": c.get("kind")} for c in cited],
            "removal_accepted": removal_accepted, "removal_strength": strength,
            "duplicate_of": dup, "duplicate_survivor": survivor(dup, facilities) if dup else None,
            "findings": findings, "agent": payload.get("agent"), "run_id": payload.get("run_id")}


def survivor(fid: str | None, facilities: dict) -> str | None:
    seen = set()
    while fid and fid not in seen:
        seen.add(fid)
        nxt = (facilities.get(fid) or {}).get("merged_into")
        if not nxt:
            return fid
        fid = nxt
    return fid


# --- the input: survivorship without web_research -----------------------------------------------

def basis_sql(ids: list[str], release_tag: str, carried: tuple[str, ...]) -> str:
    """Every assertion about these facilities except web_research, resolved to the id it had BEFORE
    any merge (a duplicate the agent flagged may since have been merged into its survivor)."""
    classes = ",".join(f"'{c}'" for c in carried)
    tag = release_tag.replace("'", "''")
    return f"""
    SELECT COALESCE(m.facility_id, fd.facility_id) AS facility_id, a.source_key AS source_id, a.release_tag,
           COALESCE(a.source_class, '') AS source_class, COALESCE(a.date_key, '') AS retrieved_date,
           COALESCE(a.row_hash, '') AS row_hash, COALESCE(a.basis, 'none') AS basis,
           COALESCE(a.site_visit, 0) AS site_visit, COALESCE(a.confidence, 0) AS confidence,
           COALESCE(a.asserted_at, '') AS asserted_at, a.field_key AS field, a.value
    FROM fact_assertions a
    LEFT JOIN release_registry rr ON rr.release_tag = a.release_tag
    LEFT JOIN legacy_id_map m ON m.registry_hash = rr.registry_hash AND m.legacy_id = a.facility_key
    LEFT JOIN facility fd ON rr.release_tag IS NULL AND fd.facility_id = a.facility_key
    WHERE a.source_key <> 'web_research'
      AND (a.release_tag = '{tag}' OR a.source_class IN ({classes}))
      AND COALESCE(m.facility_id, fd.facility_id) IN ({_ids(ids)})"""


def rebuild_inputs(assertions: list[dict], rules: dict) -> dict[str, dict]:
    from ..enrich.promote import build
    for a in assertions:
        a["source_class"] = a["source_class"] or "?"
    rows, _ = build(assertions, rules)
    return {r["facility_id"]: {"facility_id": r["facility_id"],
                               **{f: r[f] for f in INPUT_FIELDS if r.get(f) not in (None, "")}} for r in rows}


# --- splits -------------------------------------------------------------------------------------

def _h(seed: int, fid: str) -> str:
    return hashlib.sha256(f"{seed}:{fid}".encode()).hexdigest()


def category(label: dict) -> str:
    v = label.get("verdict")
    return "removal" if v in REMOVALS else "duplicate" if v == "duplicate" else "other"


def stratum(label: dict, record: dict) -> tuple:
    return (label.get("verdict"), label.get("removal_strength") or "", bool(record.get("website")))


def split(labels: dict[str, dict], inputs: dict[str, dict], anchors: list[str], seed: int = SEED) -> dict[str, list[str]]:
    """Holdout first, then Dev A-F, each drawn from per-category queues that interleave the strata
    (verdict, removal strength, has website) in seeded-hash order. Deterministic in (ids, seed)."""
    queues: dict[str, list[str]] = {}
    for cat in ("removal", "duplicate", "other"):
        by_stratum: dict[tuple, list[str]] = {}
        for fid in sorted(labels, key=lambda f: _h(seed, f)):
            if category(labels[fid]) == cat:
                by_stratum.setdefault(stratum(labels[fid], inputs.get(fid, {})), []).append(fid)
        order, keys = [], sorted(by_stratum, key=lambda k: json.dumps(k))
        while any(by_stratum[k] for k in keys):
            for k in keys:
                if by_stratum[k]:
                    order.append(by_stratum[k].pop(0))
        queues[cat] = order

    def take(n: int) -> list[str]:
        want = {"removal": round(n * MIX["removal"]), "duplicate": round(n * MIX["duplicate"])}
        want["other"] = n - want["removal"] - want["duplicate"]
        got = []
        for cat, k in want.items():
            got += queues[cat][:k]; del queues[cat][:k]
        short = n - len(got)
        for cat in ("other", "removal", "duplicate"):          # top up from whatever is left
            extra = queues[cat][:short]; del queues[cat][:len(extra)]; got += extra; short -= len(extra)
        return sorted(got, key=lambda f: _h(seed, f))

    out = {"holdout": take(HOLDOUT)}
    for b in DEV_BATCHES:
        out[b] = take(BATCH)
    out["anchor"] = sorted(anchors, key=lambda f: _h(seed, f))[:ANCHORS]
    return out


# --- export -------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def benchmark_hash(inputs_sha: str, labels_sha: str) -> str:
    return hashlib.sha256(f"{inputs_sha}\n{labels_sha}\n".encode()).hexdigest()


def export(reader, out: Path, seed: int = SEED) -> dict:
    from ..golden_refresh import CARRIED_CLASSES
    from ..registry import load_yaml
    rules = load_yaml(ROOT / "registry" / "survivorship.yaml")
    tags = [r["release_tag"] for r in reader.query("SELECT DISTINCT release_tag FROM golden_facility")]
    if len(tags) != 1:
        raise RuntimeError(f"golden_facility holds {len(tags)} releases")
    tag = tags[0]
    facilities = {r["facility_id"]: r for r in reader.query("SELECT facility_id, status, merged_into FROM facility")}
    runs = ",".join(f"'{r}'" for r in REFERENCE_RUNS)
    subs = reader.query(f"SELECT facility_id, submitted_at, status, payload, report FROM web_research_submission "
                        f"WHERE status IN ('ingested', 'partial') AND run_id IN ({runs}) ORDER BY submitted_at")
    labels: dict[str, dict] = {}
    for s in subs:                                         # the latest accepted submission per facility
        labels[s["facility_id"]] = reference_label(_loads(s["payload"]), _loads(s["report"]), facilities)
    anchors = sorted({r["facility_id"] for r in reader.query(
        "SELECT DISTINCT permanent_facility_id AS facility_id FROM v_assertions_resolved "
        "WHERE field_key = 'adl_validated' AND lower(value) IN ('1', 'true', 'yes', 'y') "
        "AND permanent_facility_id IS NOT NULL")} & {f for f, r in facilities.items() if r["status"] == "active"})
    inputs: dict[str, dict] = {}
    wanted = set(labels) | set(anchors)
    for chunk in _chunks(wanted, 250):
        inputs.update(rebuild_inputs(reader.query(basis_sql(chunk, tag, CARRIED_CLASSES)), rules))
    missing = sorted(wanted - set(inputs))
    for fid in missing:                                    # nothing but web research ever said anything
        inputs[fid] = {"facility_id": fid}
    for fid, lab in labels.items():
        lab["anchor"] = fid in anchors
    splits = split(labels, inputs, anchors, seed)
    active = sorted({f for f, r in facilities.items() if r["status"] == "active"}
                    | {f for f in labels if facilities.get(f, {}).get("status") == "merged"})
    golden_index = reader.query("SELECT facility_key AS facility_id, name, address, city, state, zip, phone, website "
                                "FROM golden_facility ORDER BY facility_key")
    used = set().union(*splits.values())
    out.mkdir(parents=True, exist_ok=True)
    (out / "inputs.json").write_text(json.dumps(
        {"release_tag": tag, "facilities": {f: inputs[f] for f in sorted(used)}, "active": active,
         "golden_index": golden_index}, indent=1, sort_keys=True, default=str))
    (out / "labels.json").write_text(json.dumps(
        {"facilities": {f: labels.get(f) or {"verdict": None, "findings": [], "anchor": True} for f in sorted(used)},
         "anchors": sorted(anchors)}, indent=1, sort_keys=True, default=str))
    si, sl = _sha(out / "inputs.json"), _sha(out / "labels.json")
    mix = Counter(lab["verdict"] for lab in labels.values())
    manifest = {"exported_at": datetime.now(timezone.utc).isoformat(), "release_tag": tag, "seed": seed,
                "reference_runs": list(REFERENCE_RUNS), "reference_facilities": len(labels),
                "reference_mix": dict(mix),
                "strong_removals": sum(1 for lab in labels.values() if lab["removal_strength"] == "strong"),
                "weak_removals": sum(1 for lab in labels.values() if lab["removal_strength"] == "weak"),
                "anchors_available": len(anchors), "inputs_without_assertions": len(missing),
                "splits": splits, "inputs_sha256": si, "labels_sha256": sl,
                "benchmark_sha256": benchmark_hash(si, sl), "database_writes": 0,
                "run_id": os.environ.get("GITHUB_RUN_ID"), "commit": os.environ.get("GITHUB_SHA")}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def verify(folder: Path, expected: str | None = None) -> dict:
    """The manifest, after checking both files still hash to it (and to `expected`, if given)."""
    m = json.loads((folder / "manifest.json").read_text())
    si = _sha(folder / "inputs.json")
    sl = _sha(folder / "labels.json") if (folder / "labels.json").exists() else m["labels_sha256"]
    if si != m["inputs_sha256"] or sl != m["labels_sha256"]:
        raise RuntimeError("benchmark files do not match their manifest")
    if expected and m["benchmark_sha256"] != expected.strip():
        raise RuntimeError(f"benchmark hash {m['benchmark_sha256']} is not the expected {expected}")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.research_eval.benchmark")
    ap.add_argument("--out", default="benchmark")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--neon-http", action="store_true", help="read through Neon's HTTPS endpoint (local runs)")
    a = ap.parse_args(argv)
    if a.neon_http:
        reader = NeonHttpReader()
    else:
        urls = [os.environ.get(k, "").strip() for k in ("DATABASE_URL_UNPOOLED", "DATABASE_URL")]
        url = next((u for u in urls if u and u.isascii()), "")
        if not url:
            print("no database URL", file=sys.stderr); return 1
        reader = PsycopgReader(url)
    m = export(reader, Path(a.out), a.seed)
    summary = {k: v for k, v in m.items() if k != "splits"} | {"split_sizes": {k: len(v) for k, v in m["splits"].items()}}
    print(json.dumps(summary, indent=1))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"## Benchmark exported\n\nBenchmark SHA-256: `{m['benchmark_sha256']}`\n\n"
                    f"```json\n{json.dumps(summary, indent=1)}\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
