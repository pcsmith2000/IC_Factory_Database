"""Apply and verify the employee-feedback schema in one PostgreSQL transaction."""
import os

from pipeline.warehouse import PostgresWarehouse


class VerifiedWarehouse(PostgresWarehouse):
    def init_schema(self):
        with self.transaction() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '30s'")
            cursor.execute("SET LOCAL statement_timeout = '120s'")
            cursor.execute("SELECT pg_advisory_xact_lock(7419026)")
            cursor.execute("LOCK TABLE golden_facility, fact_assertions IN SHARE ROW EXCLUSIVE MODE")
            before = {}
            for table in ("golden_facility", "fact_assertions"):
                before[table] = cursor.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
            super().init_schema()
            for table, expected in before.items():
                actual = cursor.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                if actual != expected:
                    raise RuntimeError(f"{table} row count changed; rolling back migration")
            checks = {
                "feedback ledger": "SELECT to_regclass('employee_feedback') IS NOT NULL AS ok",
                "employee notes": "SELECT count(*) = 2 AS ok FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='golden_facility' AND column_name IN ('employee_notes','employee_notes__source')",
                "employee source": "SELECT EXISTS (SELECT 1 FROM dim_source WHERE source_key='adl_employee_feedback' AND class='human_feedback') AS ok",
                "feedback trigger": "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='golden_facility'::regclass AND tgname='golden_employee_feedback' AND tgenabled IN ('O','A')) AS ok",
                "preservation function": "SELECT to_regprocedure('preserve_employee_feedback()') IS NOT NULL AS ok",
                "promotion function": "SELECT to_regprocedure('replace_golden_with_feedback(jsonb,text)') IS NOT NULL AS ok",
            }
            for name, sql in checks.items():
                if not cursor.execute(sql).fetchone()["ok"]:
                    raise RuntimeError(f"Missing {name}; rolling back migration")
            self.verified_counts = before
            self.verified_checks = list(checks)


def main():
    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("PostgreSQL connection missing; migration cannot fall back to SQLite")
    warehouse = VerifiedWarehouse(url)
    warehouse.conn.close()
    lines = ["Employee feedback migration committed and verified."]
    lines.extend(f"{table}: {count} rows, unchanged" for table, count in warehouse.verified_counts.items())
    lines.extend(f"Verified: {name}" for name in warehouse.verified_checks)
    print("\n".join(lines))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
