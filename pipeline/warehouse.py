"""Layer 8 sink: the warehouse.

One star schema (docs/warehouse.md), two engines behind one loader:
  sqlite    a local file (default; standard library) — laptop and Cowork runs
  postgres  Neon today, Cloud SQL for PostgreSQL later — selected whenever DATABASE_URL is set
The SQL is written once in the dialect both engines share (ON CONFLICT upserts, TEXT/INTEGER/REAL);
only the parameter placeholder differs. An engine that is selected but cannot be opened halts
the run loudly — it is never silently skipped.

Provenance is the point. `fact_assertions` is append-only and tagged by release; `golden_facility`
is replaced per release and is a pure function of the assertions and registry/survivorship.yaml;
`ref_source_row` anchors every assertion's row_hash back to the source URL, document and row
position. The `v_provenance` view walks that chain: golden value → winning source → assertion →
contract row. Nothing here decides anything; it records what Layers 5–7 decided.

    python -m pipeline.warehouse init                      # create the schema on the configured engine
    python -m pipeline.warehouse provenance IC-00001 [--field address]
    python -m pipeline.warehouse releases
    python -m pipeline.warehouse sql "select state, count(*) from golden_facility group by 1"
"""
from __future__ import annotations
import hashlib, json, os, sqlite3, sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

# The last two are asserted only by enrichment (docs/enrichment.md). Layers 1-8 leave them NULL,
# which is the honest answer: no published source measures a building or doubts a plant's existence.
GOLDEN_FIELDS = ["name", "legal_name", "address", "city", "state", "zip", "lat_lon", "naics",
                 "status", "expiry_date", "product_type", "building_sqft", "existence_flag"]
SYNTHETIC_SOURCES = {  # assertion sources that are not registry entries
    "operator": {"name": "Human correction (control/operator_assertions.csv)", "class": "operator"},
    "lookup": {"name": "Layer 4 entity resolution", "class": "lookup"},
    # enrichment, stages 9-12: they assert into fact_assertions like any source, so dim_source needs
    # them or v_provenance answers "who says so" with a null join
    "enrich:locate": {"name": "Enrichment 9 — web-cited address", "class": "enrichment"},
    "geocode:geocodio": {"name": "Enrichment 10 — Geocodio rooftop geocode", "class": "enrichment"},
    "overture:building": {"name": "Enrichment 11 — Overture building footprint", "class": "enrichment"},
    "enrich:existence": {"name": "Enrichment 12 — existence review flag", "class": "enrichment"},
}


class WarehouseNotImplemented(Exception):
    pass


# ---------------------------------------------------------------- schema
def _golden_columns() -> str:
    return ", ".join(f"{f} TEXT, {f}__source TEXT" for f in GOLDEN_FIELDS)


DDL = [
    # asserted_at is when this ROW was written; date_key is when the SOURCE was retrieved. They are
    # different questions and survivorship needs both: retrieved_date ranks one source against
    # another, and asserted_at orders two assertions that share it. Without the second, re-measuring
    # a facility on the same day leaves two rows that `tie: most_recent` cannot separate, and which
    # one reaches golden is arbitrary — which is exactly what happened to 24 footprints.
    """CREATE TABLE IF NOT EXISTS fact_assertions (
        assertion_id TEXT NOT NULL, release_tag TEXT NOT NULL,
        facility_key TEXT NOT NULL, source_key TEXT NOT NULL, field_key TEXT NOT NULL, date_key TEXT,
        value TEXT, basis TEXT, site_visit INTEGER, row_hash TEXT, confidence REAL, source_class TEXT,
        asserted_at TEXT,
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
]

# Views are created after the golden columns are reconciled, not with the tables: they name every
# column in GOLDEN_FIELDS, so adding a field would make CREATE VIEW fail on a database whose
# golden_facility predates it. See _Warehouse.init_schema.
VIEWS = [
    # golden, one row per (facility, field): the wide table unpivoted (v_provenance depends on it: drop that first)
    "DROP VIEW IF EXISTS v_provenance",
    "DROP VIEW IF EXISTS v_golden_field",
    "CREATE VIEW v_golden_field AS " + " UNION ALL ".join(
        f"SELECT release_tag, facility_key, '{f}' AS field_key, {f} AS value, {f}__source AS source_key "
        f"FROM golden_facility WHERE {f} IS NOT NULL" for f in GOLDEN_FIELDS),
    # golden value → the assertion(s) that carried it → the contract row they came from
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
class _Cursor:
    """Executes shared-dialect SQL ('?' placeholders) on either driver; exposes rowcount and fetches."""
    def __init__(self, wh, raw):
        self.wh, self.raw = wh, raw
    def execute(self, sql: str, params=()):
        self.raw.execute(sql.replace("?", self.wh.placeholder) if self.wh.placeholder != "?" else sql, params)
        return self.raw
    def fetchone(self): return self.raw.fetchone()
    def fetchall(self): return self.raw.fetchall()


class _Warehouse:
    engine = "?"
    placeholder = "?"
    path = ""

    def existing_columns(self, table: str) -> set[str]:
        """Column names of an existing table, or an empty set if it has none yet."""
        raise NotImplementedError

    def init_schema(self):
        """Tables, then any column this build added, then the views.

        CREATE TABLE IF NOT EXISTS does not widen a table that already exists, so a database
        created before a column existed keeps its old shape and every later write of that column is
        silently dropped. The reconcile step below is what makes adding one a code change rather
        than a migration script.
        """
        with self.transaction() as c:
            for stmt in DDL:
                c.execute(stmt)
        wanted = {"golden_facility": [x for f in GOLDEN_FIELDS for x in (f, f"{f}__source")],
                  "fact_assertions": ["asserted_at"]}
        for table, want in wanted.items():
            have = self.existing_columns(table)
            with self.transaction() as c:
                for col in want:
                    if col not in have:
                        c.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" TEXT')
        with self.transaction() as c:
            for stmt in VIEWS:
                c.execute(stmt)

    @contextmanager
    def transaction(self):
        raise NotImplementedError

    def close(self):
        self.conn.close()

    def _rows(self, raw) -> list[dict]:
        raise NotImplementedError

    def load_release(self, record: dict, *, assertions: list[dict], golden: list[dict], conflicts: list[dict],
                     facilities: list[dict], rows: list[dict], registry: dict, registry_text: str,
                     rules: dict, control_rows: list[dict], control_sha: str | None, known_gaps: dict,
                     survivorship_hash: str) -> dict:
        """Load one successful release. Idempotent per release tag: loading the same release twice
        changes nothing. Facts append; golden and conflicts are replaced; dimensions upsert."""
        tag, run_ts = record["release"]["tag"], record["started"]
        with self.transaction() as c:
            # dimensions
            for s in registry.get("sources", []):
                c.execute("""INSERT INTO dim_source VALUES (?,?,?,?,?,?,?)
                             ON CONFLICT(source_key) DO UPDATE SET name=excluded.name, class=excluded.class,
                             method=excluded.method, status_basis=excluded.status_basis, status=excluded.status""",
                          (s["id"], s["id"], s.get("name"), str(s.get("class")), s.get("method"), s.get("status_basis"), s.get("status")))
            for sid, meta in SYNTHETIC_SOURCES.items():
                c.execute("INSERT INTO dim_source VALUES (?,?,?,?,?,?,?) ON CONFLICT (source_key) DO NOTHING",
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
                    c.execute("INSERT INTO dim_date VALUES (?,?,?,?) ON CONFLICT (date_key) DO NOTHING", dr)
            # provenance anchor: every reconciled contract row
            for r in rows:
                c.execute("INSERT INTO ref_source_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (row_hash) DO UPDATE SET "
                          + ", ".join(f"{k}=excluded.{k}" for k in ("source_key", "source_url", "source_document", "retrieved_date", "row_position",
                                                                   "source_identifier", "name_verbatim", "address_verbatim", "city_verbatim", "state_verbatim",
                                                                   "zip_verbatim", "facility_key", "match_method", "match_confidence", "last_seen_release")),
                          (r["row_hash"], r["source_id"], r.get("source_url"), r.get("source_document"), r.get("retrieved_date"),
                           r.get("row_position"), r.get("source_identifier"), r.get("name_verbatim"), r.get("address_verbatim"),
                           r.get("city_verbatim"), r.get("state_verbatim"), r.get("zip_verbatim"), r.get("facility_id"),
                           r.get("match_method"), _float(r.get("match_confidence")), tag))
            # the fact
            n_facts = 0
            for a in assertions:
                dr = _date_row(a.get("retrieved_date") or "")
                cur = c.execute("INSERT INTO fact_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (assertion_id, release_tag) DO NOTHING",
                                (assertion_id(a), tag, a["facility_id"], a["source_id"], a["field"], dr[0] if dr else None,
                                 a["value"], a.get("basis"), 1 if a.get("site_visit") in (True, "True") else 0,
                                 a.get("row_hash") or None, _float(a.get("confidence")), a.get("source_class"),
                                 a.get("asserted_at") or run_ts))
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
            c.execute("INSERT INTO fact_release_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (release_tag, run_ts) DO UPDATE SET "
                      "published_count=excluded.published_count, raw_count=excluded.raw_count, dup_rate=excluded.dup_rate, recall=excluded.recall, "
                      "mean_abs_bias=excluded.mean_abs_bias, gates_passed=excluded.gates_passed, gates_total=excluded.gates_total",
                      (tag, run_ts, rel.get("published_count"), rel.get("raw_count"), g1.get("details", {}).get("rate"),
                       m7.get("recall", {}).get("recall"), (m7.get("coverage_bias") or {}).get("mean_abs_bias"),
                       sum(1 for g in record.get("gates", []) if g["passed"]), len(record.get("gates", [])),
                       record.get("registry_version"), survivorship_hash, cls.get("prompt_hash"), cls.get("model"),
                       record.get("pipeline_version")))
            c.execute("INSERT INTO ref_source_registry VALUES (?,?,?,?) ON CONFLICT (release_tag) DO UPDATE SET registry_version=excluded.registry_version, registry_sha=excluded.registry_sha, yaml=excluded.yaml",
                      (tag, record.get("registry_version"), record.get("registry_file_sha"), registry_text))
            if control_sha:
                for r in control_rows:
                    c.execute("INSERT INTO ref_control VALUES (?,?,?,?,?,?,?) ON CONFLICT (checksum, control_id) DO NOTHING",
                              (control_sha, r.get("control_id"), r.get("name"), r.get("city"), r.get("state"), r.get("triage"), r.get("reason")))
            for st, cause in (known_gaps.get("states") or {}).items():
                c.execute("INSERT INTO ref_known_gaps VALUES (?,?,?,?) ON CONFLICT (release_tag, state) DO UPDATE SET cause=excluded.cause, as_of=excluded.as_of", (tag, st, cause, run_ts))
            total = self.query("SELECT count(*) AS n FROM fact_assertions")[0]["n"]
        return {"engine": self.engine, "path": str(self.path), "release_tag": tag, "assertions_appended": n_facts,
                "golden_rows": len(golden), "conflicts": len(conflicts), "source_rows": len(rows), "assertions_total": total}

    # ---- reads
    def provenance(self, facility_id: str, field: str | None = None) -> list[dict]:
        q = "SELECT * FROM v_provenance WHERE facility_key = ?" + (" AND field_key = ?" if field else "") + " ORDER BY field_key, retrieved_date"
        return self.query(q, (facility_id, field) if field else (facility_id,))

    def query(self, sql: str, params=()) -> list[dict]:
        with self.transaction() as c:
            return self._rows(c.execute(sql, params))


class SqliteWarehouse(_Warehouse):
    engine = "sqlite"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    @contextmanager
    def transaction(self):
        with self.conn:
            yield _Cursor(self, self.conn.cursor())

    def _rows(self, raw) -> list[dict]:
        return [dict(r) for r in raw.fetchall()]

    def existing_columns(self, table: str) -> set[str]:
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}


class PostgresWarehouse(_Warehouse):
    """Neon (or any Postgres). Prefers the direct/unpooled URL for the loader — DDL and one long
    transaction per release do not belong on a PgBouncer transaction-mode pool."""
    engine = "postgres"
    placeholder = "%s"

    def __init__(self, url: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise WarehouseNotImplemented("DATABASE_URL is set but psycopg is not installed: pip install -e '.[postgres]'") from e
        self.psycopg = psycopg
        self.conn = psycopg.connect(url, row_factory=dict_row)
        self.path = self._redact(url)
        self.init_schema()

    @staticmethod
    def _redact(url: str) -> str:
        import re
        return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)

    @contextmanager
    def transaction(self):
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                yield _Cursor(self, cur)

    def _rows(self, raw) -> list[dict]:
        return [dict(r) for r in raw.fetchall()] if raw.description else []

    def existing_columns(self, table: str) -> set[str]:
        rows = self.query("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,))
        return {r["column_name"] for r in rows}


def open_warehouse(cfg: dict, root: Path):
    """Engine: IC_WAREHOUSE_ENGINE env · else postgres when DATABASE_URL (or DATABASE_URL_UNPOOLED) is set ·
    else warehouse.engine in config (default sqlite). IC_WAREHOUSE_PATH overrides the SQLite path."""
    w = cfg.get("warehouse") or {}
    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    engine = os.environ.get("IC_WAREHOUSE_ENGINE") or ("postgres" if url else w.get("engine", "sqlite"))
    if engine in ("none", "off"):
        return None
    if engine == "sqlite":
        return SqliteWarehouse(root / (os.environ.get("IC_WAREHOUSE_PATH") or w.get("sqlite_path", "build/ic_factory.sqlite")))
    if engine in ("postgres", "neon"):
        if not url:
            raise WarehouseNotImplemented("warehouse engine is postgres but DATABASE_URL is not set (neon env pull, or a GitHub secret)")
        return PostgresWarehouse(url)
    raise WarehouseNotImplemented(f"unknown warehouse engine {engine!r} (sqlite | postgres | none)")


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m pipeline.warehouse")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine (DATABASE_URL → postgres, else warehouse.sqlite_path)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the schema (tables, indexes, views) on the configured engine; idempotent")
    p = sub.add_parser("provenance", help="every golden field of a facility, its winning source, and the contract row behind it")
    p.add_argument("facility_id"); p.add_argument("--field")
    sub.add_parser("releases", help="fact_release_metrics, newest first")
    q = sub.add_parser("sql", help="run a read-only query"); q.add_argument("query")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    from .registry import load_yaml
    cfg = load_yaml(root / "registry" / "config.yaml")
    if args.db:
        wh = SqliteWarehouse(Path(args.db) if Path(args.db).is_absolute() else root / args.db)
    else:
        try:
            wh = open_warehouse(cfg, root)
        except WarehouseNotImplemented as e:
            print(e, file=sys.stderr); return 1
        if wh is None:
            print("warehouse engine is 'none'", file=sys.stderr); return 1
    if args.cmd == "init":
        print(f"schema ready on {wh.engine}: {wh.path}"); return 0
    if args.cmd == "provenance":
        rows = wh.provenance(args.facility_id, args.field)
        if not rows:
            print(f"{args.facility_id}: not in golden_facility"); return 1
        for r in rows:
            where = f"{r['source_document'] or ''} row {r['row_position'] or '?'} · {r['source_url'] or ''}".strip(" ·") if r["row_hash"] else "(no contract row — operator or lookup assertion)"
            print(f"{r['field_key']:<13} = {r['value']!s:<40} ← {r['source_key']:<20} {r['retrieved_date'] or '':<10} {where}")
    elif args.cmd == "releases":
        for r in wh.query("SELECT * FROM fact_release_metrics ORDER BY run_ts DESC"):
            print(json.dumps(r, default=str))
    else:
        for r in wh.query(args.query):
            print(json.dumps(r, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
