"""Layer 8 sink: the warehouse.

One star schema (docs/warehouse.md), two engines. SQLite is the engine today: a single file,
no server, built from the standard library, holding exactly the tables BigQuery will hold.
BigQuery is the target architecture; selecting it (BQ_DATASET in the environment, or
`warehouse.engine: bigquery` in registry/config.yaml) halts the run loudly until the loader is
written — it is never silently skipped.

Provenance is the point. `fact_assertions` is append-only and tagged by release; `golden_facility`
is replaced per release and is a pure function of the assertions and registry/survivorship.yaml;
`ref_source_row` anchors every assertion's row_hash back to the source URL, document and row
position. The `v_provenance` view walks that chain: golden value → winning source → assertion →
contract row. Nothing here decides anything; it records what Layers 5–7 decided.

    python -m pipeline.warehouse provenance IC-00001 [--field address]
    python -m pipeline.warehouse releases
    python -m pipeline.warehouse sql "select state, count(*) from golden_facility group by 1"
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys
from datetime import date
from pathlib import Path

GOLDEN_FIELDS = ["name", "legal_name", "address", "city", "state", "zip", "lat_lon", "naics",
                 "status", "expiry_date", "product_type"]
SYNTHETIC_SOURCES = {  # assertion sources that are not registry entries
    "operator": {"name": "Human correction (control/operator_assertions.csv)", "class": "operator"},
    "lookup": {"name": "Layer 4 entity resolution", "class": "lookup"},
}


class WarehouseNotImplemented(Exception):
    pass


# ---------------------------------------------------------------- schema
def _golden_columns() -> str:
    return ", ".join(f"{f} TEXT, {f}__source TEXT" for f in GOLDEN_FIELDS)


DDL = [
    """CREATE TABLE IF NOT EXISTS fact_assertions (
        assertion_id TEXT NOT NULL, release_tag TEXT NOT NULL,
        facility_key TEXT NOT NULL, source_key TEXT NOT NULL, field_key TEXT NOT NULL, date_key TEXT,
        value TEXT, basis TEXT, site_visit INTEGER, row_hash TEXT, confidence REAL, source_class TEXT,
        PRIMARY KEY (assertion_id, release_tag))""",
    "CREATE INDEX IF NOT EXISTS ix_assertions_facility ON fact_assertions (facility_key, field_key)",
    "CREATE INDEX IF NOT EXISTS ix_assertions_row ON fact_assertions (row_hash)",
    """CREATE TABLE IF NOT EXISTS dim_facility (
        facility_key TEXT PRIMARY KEY, facility_id TEXT NOT NULL, signature TEXT,
        first_seen_release TEXT, last_seen_release TEXT, name TEXT, state TEXT, tier TEXT)""",
    """CREATE TABLE IF NOT EXISTS dim_source (
        source_key TEXT PRIMARY KEY, source_id TEXT, name TEXT, class TEXT, method TEXT,
        status_basis TEXT, status TEXT)""",
    """CREATE TABLE IF NOT EXISTS dim_field (
        field_key TEXT PRIMARY KEY, field TEXT, survivorship_order_json TEXT, survivorship_version TEXT)""",
    "CREATE TABLE IF NOT EXISTS dim_date (date_key TEXT PRIMARY KEY, date TEXT, quarter TEXT, year INTEGER)",
    f"""CREATE TABLE IF NOT EXISTS golden_facility (
        facility_key TEXT PRIMARY KEY, release_tag TEXT NOT NULL, {_golden_columns()},
        n_assertions INTEGER, n_sources INTEGER)""",
    """CREATE TABLE IF NOT EXISTS conflicts (
        release_tag TEXT, facility_key TEXT, field_key TEXT, winner TEXT, winner_source TEXT,
        n_values INTEGER, values_seen TEXT, PRIMARY KEY (release_tag, facility_key, field_key))""",
    """CREATE TABLE IF NOT EXISTS fact_release_metrics (
        release_tag TEXT, run_ts TEXT, published_count INTEGER, raw_count INTEGER, dup_rate REAL,
        recall REAL, mean_abs_bias REAL, gates_passed INTEGER, gates_total INTEGER,
        registry_version TEXT, survivorship_hash TEXT, prompt_hash TEXT, model TEXT, pipeline_version TEXT,
        PRIMARY KEY (release_tag, run_ts))""",
    """CREATE TABLE IF NOT EXISTS ref_control (
        checksum TEXT, control_id TEXT, name TEXT, city TEXT, state TEXT, triage TEXT, reason TEXT,
        PRIMARY KEY (checksum, control_id))""",
    """CREATE TABLE IF NOT EXISTS ref_source_registry (
        release_tag TEXT PRIMARY KEY, registry_version TEXT, registry_sha TEXT, yaml TEXT)""",
    """CREATE TABLE IF NOT EXISTS ref_known_gaps (
        release_tag TEXT, state TEXT, cause TEXT, as_of TEXT, PRIMARY KEY (release_tag, state))""",
    """CREATE TABLE IF NOT EXISTS ref_source_row (
        row_hash TEXT PRIMARY KEY, source_key TEXT, source_url TEXT, source_document TEXT,
        retrieved_date TEXT, row_position TEXT, source_identifier TEXT,
        name_verbatim TEXT, address_verbatim TEXT, city_verbatim TEXT, state_verbatim TEXT, zip_verbatim TEXT,
        facility_key TEXT, match_method TEXT, match_confidence REAL, last_seen_release TEXT)""",
    # golden, one row per (facility, field): the wide table unpivoted
    "DROP VIEW IF EXISTS v_golden_field",
    "CREATE VIEW v_golden_field AS " + " UNION ALL ".join(
        f"SELECT release_tag, facility_key, '{f}' AS field_key, {f} AS value, {f}__source AS source_key "
        f"FROM golden_facility WHERE {f} IS NOT NULL" for f in GOLDEN_FIELDS),
    # golden value → the assertion(s) that carried it → the contract row they came from
    "DROP VIEW IF EXISTS v_provenance",
    """CREATE VIEW v_provenance AS
        SELECT g.release_tag, g.facility_key, g.field_key, g.value, g.source_key,
               a.assertion_id, a.date_key AS retrieved_date, a.basis, a.site_visit, a.confidence, a.row_hash,
               r.source_url, r.source_document, r.row_position, r.source_identifier
        FROM v_golden_field g
        LEFT JOIN fact_assertions a
          ON a.release_tag = g.release_tag AND a.facility_key = g.facility_key
         AND a.field_key = g.field_key AND a.value = g.value AND a.source_key = g.source_key
        LEFT JOIN ref_source_row r ON r.row_hash = a.row_hash""",
]


def assertion_id(a: dict) -> str:
    h = hashlib.sha256()
    for k in ("facility_id", "field", "value", "source_id", "retrieved_date", "row_hash"):
        h.update(str(a.get(k) or "").encode()); h.update(b"\x1f")
    return h.hexdigest()[:16]


def _date_row(d: str) -> tuple | None:
    try:
        dt = date.fromisoformat(d[:10])
    except (ValueError, TypeError):
        return None
    return (d[:10], d[:10], f"{dt.year}Q{(dt.month - 1) // 3 + 1}", dt.year)


def _float(x) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- engines
class SqliteWarehouse:
    engine = "sqlite"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            for stmt in DDL:
                self.conn.execute(stmt)

    def close(self):
        self.conn.close()

    def load_release(self, record: dict, *, assertions: list[dict], golden: list[dict], conflicts: list[dict],
                     facilities: list[dict], rows: list[dict], registry: dict, registry_text: str,
                     rules: dict, control_rows: list[dict], control_sha: str | None, known_gaps: dict,
                     survivorship_hash: str) -> dict:
        """Load one successful release. Idempotent per release tag: loading the same release twice
        changes nothing. Facts append; golden and conflicts are replaced; dimensions upsert."""
        tag, run_ts = record["release"]["tag"], record["started"]
        c = self.conn
        with c:
            # dimensions
            for s in registry.get("sources", []):
                c.execute("""INSERT INTO dim_source VALUES (?,?,?,?,?,?,?)
                             ON CONFLICT(source_key) DO UPDATE SET name=excluded.name, class=excluded.class,
                             method=excluded.method, status_basis=excluded.status_basis, status=excluded.status""",
                          (s["id"], s["id"], s.get("name"), str(s.get("class")), s.get("method"), s.get("status_basis"), s.get("status")))
            for sid, meta in SYNTHETIC_SOURCES.items():
                c.execute("INSERT OR IGNORE INTO dim_source VALUES (?,?,?,?,?,?,?)",
                          (sid, sid, meta["name"], meta["class"], None, None, "active"))
            c.execute("DELETE FROM dim_field")
            for f in GOLDEN_FIELDS:
                order = rules.get("fields", {}).get(f, {}).get("order", rules.get("default_order", []))
                c.execute("INSERT INTO dim_field VALUES (?,?,?,?)", (f, f, json.dumps(order), str(rules.get("version"))))
            for f in facilities:
                c.execute("""INSERT INTO dim_facility VALUES (?,?,?,?,?,?,?,?)
                             ON CONFLICT(facility_key) DO UPDATE SET last_seen_release=excluded.last_seen_release,
                             signature=excluded.signature, name=excluded.name, state=excluded.state, tier=excluded.tier""",
                          (f["facility_id"], f["facility_id"], f.get("signature"), tag, tag, f.get("name"), f.get("state"), f.get("tier")))
            for d in {a.get("retrieved_date") for a in assertions} | {r.get("retrieved_date") for r in rows}:
                dr = _date_row(d or "")
                if dr:
                    c.execute("INSERT OR IGNORE INTO dim_date VALUES (?,?,?,?)", dr)
            # provenance anchor: every reconciled contract row
            for r in rows:
                c.execute("INSERT OR REPLACE INTO ref_source_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (r["row_hash"], r["source_id"], r.get("source_url"), r.get("source_document"), r.get("retrieved_date"),
                           r.get("row_position"), r.get("source_identifier"), r.get("name_verbatim"), r.get("address_verbatim"),
                           r.get("city_verbatim"), r.get("state_verbatim"), r.get("zip_verbatim"), r.get("facility_id"),
                           r.get("match_method"), _float(r.get("match_confidence")), tag))
            # the fact
            n_facts = 0
            for a in assertions:
                dr = _date_row(a.get("retrieved_date") or "")
                cur = c.execute("INSERT OR IGNORE INTO fact_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (assertion_id(a), tag, a["facility_id"], a["source_id"], a["field"], dr[0] if dr else None,
                                 a["value"], a.get("basis"), 1 if a.get("site_visit") in (True, "True") else 0,
                                 a.get("row_hash") or None, _float(a.get("confidence")), a.get("source_class")))
                n_facts += cur.rowcount
            # golden: replaced, never edited
            c.execute("DELETE FROM golden_facility")
            cols = ["facility_key", "release_tag"] + [x for f in GOLDEN_FIELDS for x in (f, f"{f}__source")] + ["n_assertions", "n_sources"]
            for g in golden:
                vals = [g["facility_id"], tag] + [g.get(x) for f in GOLDEN_FIELDS for x in (f, f"{f}__source")] + [g.get("n_assertions"), g.get("n_sources")]
                c.execute(f"INSERT INTO golden_facility ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
            c.execute("DELETE FROM conflicts WHERE release_tag = ?", (tag,))
            for k in conflicts:
                c.execute("INSERT INTO conflicts VALUES (?,?,?,?,?,?,?)",
                          (tag, k["facility_id"], k["field"], k["winner"], k["winner_source"], k["n_values"], k["values"]))
            # release metrics and versioned references
            rel, g1 = record["release"], next((g for g in record.get("gates", []) if g["gate"].startswith("G1")), {})
            m7, cls = record.get("layers", {}).get("7_measure", {}), record.get("layers", {}).get("3_classify") or {}
            c.execute("INSERT OR REPLACE INTO fact_release_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (tag, run_ts, rel.get("published_count"), rel.get("raw_count"), g1.get("details", {}).get("rate"),
                       m7.get("recall", {}).get("recall"), (m7.get("coverage_bias") or {}).get("mean_abs_bias"),
                       sum(1 for g in record.get("gates", []) if g["passed"]), len(record.get("gates", [])),
                       record.get("registry_version"), survivorship_hash, cls.get("prompt_hash"), cls.get("model"),
                       record.get("pipeline_version")))
            c.execute("INSERT OR REPLACE INTO ref_source_registry VALUES (?,?,?,?)",
                      (tag, record.get("registry_version"), record.get("registry_file_sha"), registry_text))
            if control_sha:
                for r in control_rows:
                    c.execute("INSERT OR IGNORE INTO ref_control VALUES (?,?,?,?,?,?,?)",
                              (control_sha, r.get("control_id"), r.get("name"), r.get("city"), r.get("state"), r.get("triage"), r.get("reason")))
            for st, cause in (known_gaps.get("states") or {}).items():
                c.execute("INSERT OR REPLACE INTO ref_known_gaps VALUES (?,?,?,?)", (tag, st, cause, run_ts))
        return {"engine": self.engine, "path": str(self.path), "release_tag": tag, "assertions_appended": n_facts,
                "golden_rows": len(golden), "conflicts": len(conflicts), "source_rows": len(rows),
                "assertions_total": c.execute("SELECT count(*) FROM fact_assertions").fetchone()[0]}

    # ---- reads
    def provenance(self, facility_id: str, field: str | None = None) -> list[dict]:
        q = "SELECT * FROM v_provenance WHERE facility_key = ?" + (" AND field_key = ?" if field else "") + " ORDER BY field_key, retrieved_date"
        return [dict(r) for r in self.conn.execute(q, (facility_id, field) if field else (facility_id,))]

    def query(self, sql: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql)]


def open_warehouse(cfg: dict, root: Path):
    """Engine from the environment first (BQ_DATASET, IC_WAREHOUSE_ENGINE, IC_WAREHOUSE_PATH), then config."""
    w = cfg.get("warehouse") or {}
    engine = os.environ.get("IC_WAREHOUSE_ENGINE") or ("bigquery" if os.environ.get("BQ_DATASET") else w.get("engine", "sqlite"))
    if engine in ("none", "off"):
        return None
    if engine == "sqlite":
        return SqliteWarehouse(root / (os.environ.get("IC_WAREHOUSE_PATH") or w.get("sqlite_path", "build/ic_factory.sqlite")))
    if engine == "bigquery":
        raise WarehouseNotImplemented(
            f"BigQuery loader not written (BQ_DATASET={os.environ.get('BQ_DATASET') or w.get('bq_dataset')!r}). "
            "Same star schema as the SQLite engine — see docs/warehouse.md. Unset BQ_DATASET or set warehouse.engine: sqlite.")
    raise WarehouseNotImplemented(f"unknown warehouse engine {engine!r}")


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m pipeline.warehouse")
    ap.add_argument("--db", default=None, help="sqlite path (default: warehouse.sqlite_path in registry/config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("provenance", help="every golden field of a facility, its winning source, and the contract row behind it")
    p.add_argument("facility_id"); p.add_argument("--field")
    sub.add_parser("releases", help="fact_release_metrics, newest first")
    q = sub.add_parser("sql", help="run a read-only query"); q.add_argument("query")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    db = args.db
    if not db:
        from .registry import load_yaml
        db = (load_yaml(root / "registry" / "config.yaml").get("warehouse") or {}).get("sqlite_path", "build/ic_factory.sqlite")
    path = Path(db) if Path(db).is_absolute() else root / db
    if not path.exists():
        print(f"no warehouse at {path} — run the pipeline first", file=sys.stderr); return 1
    wh = SqliteWarehouse(path)
    if args.cmd == "provenance":
        rows = wh.provenance(args.facility_id, args.field)
        if not rows:
            print(f"{args.facility_id}: not in golden_facility"); return 1
        for r in rows:
            where = f"{r['source_document'] or ''} row {r['row_position'] or '?'} · {r['source_url'] or ''}".strip(" ·") if r["row_hash"] else "(no contract row — operator or lookup assertion)"
            print(f"{r['field_key']:<13} = {r['value']!s:<40} ← {r['source_key']:<20} {r['retrieved_date'] or '':<10} {where}")
    elif args.cmd == "releases":
        for r in wh.query("SELECT * FROM fact_release_metrics ORDER BY run_ts DESC"):
            print(json.dumps(r))
    else:
        for r in wh.query(args.query):
            print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
