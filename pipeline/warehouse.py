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
                 "status", "expiry_date", "product_type",
                 "website", "sq_ft", "operating_status",      # source-stated; added 2026-09-17
                 "phone", "email",                            # source-stated; added 2026-09-18
                 # ADL's own plant lists, 2026-09-18. adl_validated is what the front end's
                 # "ADL validated" toggle filters the table and the map on.
                 "adl_validated", "primary_capability", "secondary_capability", "material",
                 "sector", "throughput", "throughput_unit", "utilisation_pct", "vacant_capacity",
                 "annual_revenue_usd", "automation_level", "states_serviced", "country_based",
                 "value_basis",
                 # measured/concluded by stages 11-12, and employee_notes from the feedback path
                 "building_sqft", "existence_flag", "employee_notes",
                 # 2026-09-23: the one square-footage number, chosen across sq_ft (what a source
                 # states) and building_sqft (what the Overture footprint measures) under the
                 # `derived` rule in registry/survivorship.yaml. Its __source says which won.
                 "floor_area_sqft",
                 # stage 15, over registry/taxonomy.yaml. Two columns rather than one because the
                 # two are measured differently: the group carries a real accuracy over ADL's 218
                 # labels and most leaves have too few examples to score at all.
                 "capability_group", "capability_leaf"]
def dim_field_rows(rules: dict) -> list[tuple]:
    """dim_field: one row per field_key fact_assertions can hold. A golden field carries the order
    survivorship actually ranks it by: its `fields:` rule, else its `derived:` rule, else the
    default. An operational flag (`flag_fields:`, e.g. geocode_quality) never reaches golden and
    carries NULL — it still needs a row, or a join fact_assertions ⋈ dim_field drops its facts."""
    version, fields, derived = str(rules.get("version")), rules.get("fields") or {}, rules.get("derived") or {}
    rows = [(f, f, json.dumps((fields.get(f) or derived.get(f) or {}).get("order", rules.get("default_order", []))), version)
            for f in GOLDEN_FIELDS]
    rows += [(f, f, None, version) for f in rules.get("flag_fields") or [] if f not in GOLDEN_FIELDS]
    return rows


SYNTHETIC_SOURCES = {  # assertion sources that are not registry entries
    "tako_ai_search": {"name": "Tako AI Search", "class": "tako_ai_search"},
    "adl_employee_feedback": {"name": "ADL employee feedback", "class": "human_feedback"},
    "operator": {"name": "Human correction (control/operator_assertions.csv)", "class": "operator"},
    "lookup": {"name": "Layer 4 entity resolution", "class": "lookup"},
    "classifier": {"name": "Layer 3 classifier (IC product type)", "class": "classifier"},
    # enrichment, stages 9-12: they assert into fact_assertions like any source, so dim_source needs
    # them or v_provenance answers "who says so" with a null join
    "enrich:locate": {"name": "Enrichment 9 — web-cited address", "class": "enrichment"},
    "geocode:geocodio": {"name": "Enrichment 10 — Geocodio rooftop geocode", "class": "enrichment"},
    "overture:building": {"name": "Enrichment 11 — Overture building footprint", "class": "enrichment"},
    "enrich:existence": {"name": "Enrichment 12 — existence review flag", "class": "enrichment"},
    "overture:place": {"name": "Enrichment 13 — Overture place at the same address", "class": "enrichment"},
    # Stage 15's own class, not "enrichment". Survivorship names it directly so the model can sit
    # BELOW class D — ADL's plant lists, which carry the primary_capability this stage is scored
    # against — while every other enrichment stage stays where it is.
    "capability": {"name": "Enrichment 15 — capability over registry/taxonomy.yaml",
                   "class": "capability"},
}


class WarehouseUnreachable(RuntimeError):
    """The warehouse is configured but the connection failed — bad credential, host or network.

    Separate from WarehouseNotImplemented (a missing driver or an unknown engine) because the
    caller treats them the same way and the operator does not: one is a code/deploy problem, the
    other is a secret to correct.
    """


class WarehouseNotImplemented(Exception):
    pass


# ---------------------------------------------------------------- schema
def _golden_columns() -> str:
    return ", ".join(f"{f} TEXT, {f}__source TEXT" for f in GOLDEN_FIELDS)


DDL = [
    """CREATE TABLE IF NOT EXISTS employee_feedback (feedback_id TEXT PRIMARY KEY,
        facility_key TEXT NOT NULL, employee_name TEXT NOT NULL, auth_method TEXT NOT NULL,
        channel TEXT NOT NULL, note TEXT NOT NULL, changes_json TEXT NOT NULL, created_at TEXT NOT NULL,
        request_hash TEXT NOT NULL, creates_facility INTEGER NOT NULL DEFAULT 0)""",
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
    # ---- the permanent facility registry (epic #39; pipeline/facility_registry.py) ----------------
    # An IC-number is minted once, here, and never reissued. id_registry.json minted per git
    # branch and every fork handed the same next numbers to different plants; the registry now
    # lives in one place and a plant may carry many match keys (a spelling, an address) without
    # ever being given a second number.
    """CREATE TABLE IF NOT EXISTS facility (
        facility_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'merged', 'retired')),
        merged_into TEXT REFERENCES facility (facility_id), created_at TEXT NOT NULL, created_by TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS facility_match_key (
        match_key TEXT PRIMARY KEY, facility_id TEXT NOT NULL REFERENCES facility (facility_id),
        method TEXT NOT NULL, confidence REAL, source TEXT, first_seen TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_match_key_facility ON facility_match_key (facility_id)",
    """CREATE TABLE IF NOT EXISTS facility_event (
        event_id TEXT PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, facility_id TEXT NOT NULL,
        other_facility_id TEXT, match_key TEXT, actor TEXT NOT NULL, reason TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_facility_event_facility ON facility_event (facility_id)",
    """CREATE TABLE IF NOT EXISTS legacy_id_map (
        registry_hash TEXT NOT NULL, legacy_id TEXT NOT NULL,
        facility_id TEXT REFERENCES facility (facility_id), method TEXT NOT NULL, confidence REAL,
        PRIMARY KEY (registry_hash, legacy_id))""",
    # Which id registry each release was built from (its tag's `+ids.<hash>`), so a view can join
    # fact_assertions to legacy_id_map in SQL both engines share. Written by pipeline/legacy_ids.py.
    "CREATE TABLE IF NOT EXISTS release_registry (release_tag TEXT PRIMARY KEY, registry_hash TEXT NOT NULL)",
    # Continuous golden (#44): facilities whose assertions changed since golden last saw them.
    # Filled by a trigger on fact_assertions (below, per engine) and by facility merges; drained
    # by pipeline/golden_refresh.py. Raw (facility_key, release_tag): the refresh resolves each
    # to its permanent facility, so the trigger needs no knowledge of the registry.
    """CREATE TABLE IF NOT EXISTS golden_dirty (
        facility_key TEXT NOT NULL, release_tag TEXT NOT NULL, since TEXT NOT NULL,
        PRIMARY KEY (facility_key, release_tag))""",
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
    "DROP VIEW IF EXISTS v_assertions_resolved",
    # Every fact with the permanent facility it meant (#42): through the release's id registry to
    # legacy_id_map, then one hop of merged_into (a merge always points at a live root, #43).
    # NULL permanent_facility_id: the number is unresolved, so the fact belongs to no plant.
    # A release with no release_registry row was loaded through the permanent registry (#43): its
    # facility_key already IS the permanent id, so it resolves directly (resolve_method 'direct').
    """CREATE VIEW v_assertions_resolved AS
        SELECT a.*, rr.registry_hash,
               COALESCE(m.method, CASE WHEN fd.facility_id IS NOT NULL THEN 'direct' END) AS resolve_method,
               COALESCE(fm.merged_into, m.facility_id, fd.merged_into, fd.facility_id) AS permanent_facility_id
        FROM fact_assertions a
        LEFT JOIN release_registry rr ON rr.release_tag = a.release_tag
        LEFT JOIN legacy_id_map m ON m.registry_hash = rr.registry_hash AND m.legacy_id = a.facility_key
        LEFT JOIN facility fm ON fm.facility_id = m.facility_id
        LEFT JOIN facility fd ON rr.release_tag IS NULL AND fd.facility_id = a.facility_key""",
]


def assertion_id(a: dict) -> str:
    if a.get("source_id") == "adl_employee_feedback" and a.get("assertion_id"):
        return a["assertion_id"]
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
    CHUNK = 1000

    def __init__(self, wh, raw):
        self.wh, self.raw = wh, raw
    def _sql(self, sql: str) -> str:
        return sql.replace("?", self.wh.placeholder) if self.wh.placeholder != "?" else sql
    def execute(self, sql: str, params=()):
        self.raw.execute(self._sql(sql), params)
        return self.raw
    def executemany(self, sql: str, seq):
        """Same statement, same parameters, one round trip per CHUNK rows instead of per row.

        Layer 8 writes tens of thousands of rows inside a single transaction, and against Neon each
        individual INSERT returns in ~2ms — the cost was never the database, it was the number of
        serial round trips. Nothing about the SQL or the result changes.

        Callers must NOT read rowcount off this to learn how many rows an ON CONFLICT ... DO NOTHING
        actually inserted: psycopg and sqlite3 do not agree on what executemany's rowcount means.
        Count before and after instead (see load_release).
        """
        rows = list(seq)
        if not rows:
            return self.raw
        stmt = self._sql(sql)
        for i in range(0, len(rows), self.CHUNK):
            self.raw.executemany(stmt, rows[i:i + self.CHUNK])
        return self.raw
    def fetchone(self): return self.raw.fetchone()
    def fetchall(self): return self.raw.fetchall()


class _Warehouse:
    engine = "?"
    placeholder = "?"
    path = ""

    def existing_columns(self, table: str) -> set[str]:
        """Column names of an existing table, from outside a transaction.

        _existing_columns is the form the migration uses, because it runs inside the cursor that
        is doing the ALTERs. This is the same question asked from the outside, which is what a
        test or a caller checking whether a migration landed actually wants.
        """
        with self.transaction() as c:
            return self._existing_columns(c, table)

    def init_schema(self):
        """Tables, then any column this build added, then the views — in that order.

        CREATE TABLE IF NOT EXISTS does not widen a table that already exists, so a database
        created before a column existed keeps its old shape and every later write of that column
        is silently dropped. The migration below is what makes adding a field a code change
        rather than a migration script.

        The order matters and is not cosmetic. v_golden_field selects every golden column by
        name, and Postgres validates a view's columns at CREATE — so on a warehouse laid down
        before a column existed, creating the view first fails the whole init. SQLite only
        resolves a view when it is read, which is why a test on SQLite passed with the migration
        in the wrong place.
        """
        with self.transaction() as c:
            for stmt in DDL:                     # tables only; VIEWS is a separate list
                c.execute(stmt)
            self._migrate_golden(c)              # widen them before anything selects by name
            for stmt in VIEWS:                   # drops and recreates, so a widened table is seen
                c.execute(stmt)
            # The IC-number counter. Postgres: a sequence, so two writers can never draw the same
            # number. SQLite (one writer by construction): a one-row table. Created at the floor
            # and never lowered; pipeline/facility_registry.py owns it from here.
            from .facility_registry import ID_FLOOR
            if self.engine == "postgres":
                c.execute(f"CREATE SEQUENCE IF NOT EXISTS facility_id_seq START WITH {ID_FLOOR} MINVALUE 1")
                # One statement-level trigger: a 40,000-row release load enqueues each distinct
                # (facility, release) once, not once per row.
                c.execute("""CREATE OR REPLACE FUNCTION golden_mark_dirty() RETURNS trigger LANGUAGE plpgsql AS $fn$
                    BEGIN
                      INSERT INTO golden_dirty (facility_key, release_tag, since)
                      SELECT DISTINCT facility_key, release_tag, to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
                      FROM new_rows ON CONFLICT (facility_key, release_tag) DO NOTHING;
                      RETURN NULL;
                    END $fn$""")
                c.execute("DROP TRIGGER IF EXISTS golden_dirty_on_fact ON fact_assertions")
                c.execute("""CREATE TRIGGER golden_dirty_on_fact AFTER INSERT ON fact_assertions
                             REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION golden_mark_dirty()""")
            else:
                c.execute("CREATE TABLE IF NOT EXISTS facility_id_counter (name TEXT PRIMARY KEY, next INTEGER NOT NULL)")
                c.execute("INSERT INTO facility_id_counter VALUES ('facility_id', ?) ON CONFLICT (name) DO NOTHING", (ID_FLOOR,))
                c.execute("""CREATE TRIGGER IF NOT EXISTS golden_dirty_on_fact AFTER INSERT ON fact_assertions
                             BEGIN
                               INSERT INTO golden_dirty (facility_key, release_tag, since)
                               VALUES (NEW.facility_key, NEW.release_tag, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                               ON CONFLICT (facility_key, release_tag) DO NOTHING;
                             END""")
            if self.engine == "postgres":
                # Execute raw: the migration contains Postgres's JSON existence operator '?'.
                migration = Path(__file__).resolve().parent / "migrations" / "001_employee_feedback.sql"
                c.raw.execute(migration.read_text())

    def _existing_columns(self, c, table: str) -> set[str]:
        raise NotImplementedError

    # Widened beyond golden_facility when the enrichment stages merged in. asserted_at is when a
    # ROW was written, as distinct from date_key, when its SOURCE was retrieved. Survivorship needs
    # both: retrieved_date ranks one source against another, asserted_at separates two assertions
    # that share it. Without the second, re-measuring a facility on the same day leaves two rows
    # `tie: most_recent` cannot order, and which one reaches golden is arbitrary — which is exactly
    # what happened to 24 footprints.
    MIGRATE = {"golden_facility": None,               # None: every GOLDEN_FIELD and its __source
               "fact_assertions": ["asserted_at"]}

    def _migrate_golden(self, c) -> list[str]:
        """Add any column the live tables predate.

        CREATE TABLE IF NOT EXISTS is a no-op on a table that exists, so a field added to
        GOLDEN_FIELDS after the first release would be in the INSERT column list and absent from
        the table — every load after that would fail. Each missing column and its __source twin
        are added in place; nothing is dropped or rewritten, and a table that already has them is
        left exactly as it was. Both engines accept ALTER TABLE ... ADD COLUMN ... TEXT."""
        added = []
        for table, cols in self.MIGRATE.items():
            want = cols if cols is not None else [x for f in GOLDEN_FIELDS
                                                  for x in (f, f"{f}__source")]
            have = self._existing_columns(c, table)
            for col in want:
                if col not in have:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" TEXT')
                    added.append(f"{table}.{col}")
        return added

    @contextmanager
    def transaction(self):
        raise NotImplementedError

    def close(self):
        self.conn.close()

    def _rows(self, raw) -> list[dict]:
        raise NotImplementedError

    def _assertion_count(self, c, tag: str) -> int:
        """Facts already held for this release tag. Used either side of the fact_assertions load so
        `assertions_appended` counts what the ON CONFLICT actually inserted."""
        return int(self._rows(c.execute("SELECT count(*) AS n FROM fact_assertions WHERE release_tag = ?", (tag,)))[0]["n"])

    def load_release(self, record: dict, *, assertions: list[dict], golden: list[dict], conflicts: list[dict],
                     facilities: list[dict], rows: list[dict], registry: dict, registry_text: str,
                     rules: dict, control_rows: list[dict], control_sha: str | None, known_gaps: dict,
                     survivorship_hash: str) -> dict:
        """Load one successful release. Idempotent per release tag: loading the same release twice
        changes nothing. Facts append; golden and conflicts are replaced; dimensions upsert."""
        tag, run_ts = record["release"]["tag"], record["started"]
        with self.transaction() as c:
            if self.engine == "postgres":
                c.execute("SELECT pg_advisory_xact_lock(7419026)")
            from .feedback import carry_forward
            from .golden import build_golden
            assertions, facilities = carry_forward(self, c, assertions, facilities)
            golden, conflicts = build_golden(assertions, rules)
            # dimensions
            c.executemany("""INSERT INTO dim_source VALUES (?,?,?,?,?,?,?)
                             ON CONFLICT(source_key) DO UPDATE SET name=excluded.name, class=excluded.class,
                             method=excluded.method, status_basis=excluded.status_basis, status=excluded.status""",
                          [(s["id"], s["id"], s.get("name"), str(s.get("class")), s.get("method"), s.get("status_basis"), s.get("status"))
                           for s in registry.get("sources", [])])
            c.executemany("INSERT INTO dim_source VALUES (?,?,?,?,?,?,?) ON CONFLICT (source_key) DO NOTHING",
                          [(sid, sid, meta["name"], meta["class"], None, None, "active") for sid, meta in SYNTHETIC_SOURCES.items()])
            c.execute("DELETE FROM dim_field")
            c.executemany("INSERT INTO dim_field VALUES (?,?,?,?)", dim_field_rows(rules))
            c.executemany("""INSERT INTO dim_facility VALUES (?,?,?,?,?,?,?,?)
                             ON CONFLICT(facility_key) DO UPDATE SET last_seen_release=excluded.last_seen_release,
                             signature=excluded.signature, name=excluded.name, state=excluded.state, tier=excluded.tier""",
                          [(f["facility_id"], f["facility_id"], f.get("signature"), tag, tag, f.get("name"), f.get("state"), f.get("tier"))
                           for f in facilities])
            c.executemany("INSERT INTO dim_date VALUES (?,?,?,?) ON CONFLICT (date_key) DO NOTHING",
                          [dr for dr in (_date_row(d or "") for d in
                                         {a.get("retrieved_date") for a in assertions} | {r.get("retrieved_date") for r in rows}) if dr])
            # provenance anchor: every reconciled contract row
            c.executemany("INSERT INTO ref_source_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (row_hash) DO UPDATE SET "
                          + ", ".join(f"{k}=excluded.{k}" for k in ("source_key", "source_url", "source_document", "retrieved_date", "row_position",
                                                                   "source_identifier", "name_verbatim", "address_verbatim", "city_verbatim", "state_verbatim",
                                                                   "zip_verbatim", "facility_key", "match_method", "match_confidence", "last_seen_release")),
                          [(r["row_hash"], r["source_id"], r.get("source_url"), r.get("source_document"), r.get("retrieved_date"),
                            r.get("row_position"), r.get("source_identifier"), r.get("name_verbatim"), r.get("address_verbatim"),
                            r.get("city_verbatim"), r.get("state_verbatim"), r.get("zip_verbatim"), r.get("facility_id"),
                            r.get("match_method"), _float(r.get("match_confidence")), tag) for r in rows])
            # the fact. ON CONFLICT DO NOTHING means rows offered != rows inserted, and the two
            # drivers do not agree on what executemany reports in rowcount, so the number that goes
            # on to fact_release_metrics is measured against the table rather than inferred.
            #
            # 13 columns, not 12: asserted_at is the last. It is when this ROW was written, as
            # against date_key, when its SOURCE was retrieved, and survivorship needs both — see
            # MIGRATE above.
            before = self._assertion_count(c, tag)
            c.executemany("INSERT INTO fact_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (assertion_id, release_tag) DO NOTHING",
                          [(assertion_id(a), tag, a["facility_id"], a["source_id"], a["field"],
                            (_date_row(a.get("retrieved_date") or "") or (None,))[0],
                            a["value"], a.get("basis"), 1 if a.get("site_visit") in (True, "True") else 0,
                            a.get("row_hash") or None, _float(a.get("confidence")), a.get("source_class"),
                            a.get("asserted_at") or run_ts) for a in assertions])
            n_facts = self._assertion_count(c, tag) - before
            # golden: replaced, never edited
            c.execute("DELETE FROM golden_facility")
            cols = ["facility_key", "release_tag"] + [x for f in GOLDEN_FIELDS for x in (f, f"{f}__source")] + ["n_assertions", "n_sources"]
            c.executemany(f"INSERT INTO golden_facility ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [tuple([g["facility_id"], tag] + [g.get(x) for f in GOLDEN_FIELDS for x in (f, f"{f}__source")]
                                 + [g.get("n_assertions"), g.get("n_sources")]) for g in golden])
            c.execute("DELETE FROM conflicts WHERE release_tag = ?", (tag,))
            c.executemany("INSERT INTO conflicts VALUES (?,?,?,?,?,?,?)",
                          [(tag, k["facility_id"], k["field"], k["winner"], k["winner_source"], k["n_values"], k["values"]) for k in conflicts])
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
                c.executemany("INSERT INTO ref_control VALUES (?,?,?,?,?,?,?) ON CONFLICT (checksum, control_id) DO NOTHING",
                              [(control_sha, r.get("control_id"), r.get("name"), r.get("city"), r.get("state"), r.get("triage"), r.get("reason"))
                               for r in control_rows])
            c.executemany("INSERT INTO ref_known_gaps VALUES (?,?,?,?) ON CONFLICT (release_tag, state) DO UPDATE SET cause=excluded.cause, as_of=excluded.as_of",
                          [(tag, st, cause, run_ts) for st, cause in (known_gaps.get("states") or {}).items()])
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

    def _existing_columns(self, c, table: str) -> set[str]:
        return {r["name"] for r in self._rows(c.execute(f"PRAGMA table_info({table})"))}


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

    def _existing_columns(self, c, table: str) -> set[str]:
        return {r["column_name"] for r in self._rows(c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,)))}


def open_warehouse(cfg: dict, root: Path):
    """Engine: IC_WAREHOUSE_ENGINE env · else postgres when DATABASE_URL (or DATABASE_URL_UNPOOLED) is set ·
    else warehouse.engine in config (default sqlite). IC_WAREHOUSE_PATH overrides the SQLite path."""
    w = cfg.get("warehouse") or {}
    # UNPOOLED wins, and which one won is worth saying out loud. Run 35393440727 died on
    # "password authentication failed for user 'neondb_owner'" with a perfectly good
    # DATABASE_URL set: DATABASE_URL_UNPOOLED held a different role's password and is read
    # first, so the working URL was never tried. The error named the host and the role, and
    # neither of those is the thing you have to go and fix.
    var = "DATABASE_URL_UNPOOLED" if os.environ.get("DATABASE_URL_UNPOOLED") else "DATABASE_URL"
    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    engine = os.environ.get("IC_WAREHOUSE_ENGINE") or ("postgres" if url else w.get("engine", "sqlite"))
    if engine in ("none", "off"):
        return None
    if engine == "sqlite":
        return SqliteWarehouse(root / (os.environ.get("IC_WAREHOUSE_PATH") or w.get("sqlite_path", "build/ic_factory.sqlite")))
    if engine in ("postgres", "neon"):
        if not url:
            raise WarehouseNotImplemented("warehouse engine is postgres but DATABASE_URL is not set (neon env pull, or a GitHub secret)")
        try:
            return PostgresWarehouse(url)
        except WarehouseNotImplemented:
            raise
        except Exception as e:
            raise WarehouseUnreachable(
                f"{var} did not connect: {type(e).__name__}: {str(e).strip().splitlines()[0]}\n"
                f"       url {PostgresWarehouse._redact(url)}\n"
                f"       (DATABASE_URL_UNPOOLED is read BEFORE DATABASE_URL, so a bad value there "
                f"hides a good one here)") from e
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
