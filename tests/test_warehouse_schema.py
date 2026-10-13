"""PostgresWarehouse.init_schema without a database: what it sends, and in what order."""
from contextlib import contextmanager
from pathlib import Path

from pipeline import warehouse

MIGRATIONS = Path(warehouse.__file__).resolve().parent / "migrations"


class RawCursor:
    def __init__(self, log):
        self.log, self.description = log, None
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, sql, params=()):
        self.log.append((sql, params))
        self.description = ["column_name"] if "information_schema.columns" in sql else None
    def fetchall(self):                       # every column already present: nothing to ALTER
        return [{"column_name": c} for f in warehouse.GOLDEN_FIELDS for c in (f, f"{f}__source")] + \
               [{"column_name": "asserted_at"}]


class Conn:
    def __init__(self):
        self.log = []
    @contextmanager
    def transaction(self):
        yield
    def cursor(self):
        return RawCursor(self.log)


def init():
    wh = object.__new__(warehouse.PostgresWarehouse)
    wh.conn = Conn()
    wh.init_schema()
    return [sql for sql, _ in wh.conn.log]


def test_employee_feedback_comes_only_from_its_migration():
    assert not any("employee_feedback" in s for s in warehouse.DDL)
    assert "CREATE TABLE IF NOT EXISTS employee_feedback" in (MIGRATIONS / "001_employee_feedback.sql").read_text()


def test_every_migration_runs_raw_in_name_order_after_the_base_schema():
    sent = init()
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert [f.name for f in files][:2] == ["001_employee_feedback.sql", "002_pipeline_run_requests.sql"]
    tail = sent[-len(files):]
    assert tail == [f.read_text() for f in files]                     # untranslated: 001 uses '?'
    assert " ? " in tail[0]                                       # the JSON existence operator, intact
    head = sent[:-len(files)]
    assert any("CREATE TABLE IF NOT EXISTS golden_facility" in s for s in head)
    assert any("CREATE TABLE IF NOT EXISTS dim_source" in s for s in head)


def test_placeholders_are_translated_for_psycopg():
    sent = init()
    lookup = next(s for s in sent if "information_schema.columns" in s)
    assert "table_name = %s" in lookup and "?" not in lookup


def test_pipeline_run_requests_matches_the_site():
    sql = (MIGRATIONS / "002_pipeline_run_requests.sql").read_text()
    assert ("CREATE TABLE IF NOT EXISTS pipeline_run_requests (\n"
            "      request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, employee_name TEXT NOT NULL,\n"
            "      state TEXT NOT NULL, run_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());") in sql
