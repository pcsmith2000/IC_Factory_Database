"""Run SQL against the Neon warehouse over Neon's HTTPS SQL endpoint (port 5432 is often blocked).

Read-only by default: statements run in one batch transaction with Neon-Batch-Read-Only, so any
write fails. Pass --write to allow writes (state the SQL first; see CLAUDE.md).

Connection, first match wins:
  DATABASE_URL                      a full connection string (e.g. a claude_agent role's)
  NEON_API_KEY + project id         project id from NEON_PROJECT_ID, the git-ignored .neon file,
                                    or the key's only project; role from NEON_ROLE (default: owner)

  python -m pipeline.neon_sql "SELECT count(*) FROM golden_facility"
  python -m pipeline.neon_sql --json "SELECT ..." "SELECT ..."
"""
import argparse, json, sys, urllib.error

from .db import LIVE_HOST, neon_connection_string as connection_string, neon_host, neon_http


def run(statements: list[str], write: bool = False) -> list[list[dict]]:
    url = connection_string()
    host = neon_host(url)
    if LIVE_HOST not in host:
        print(f"warning: {host} is not the live warehouse ({LIVE_HOST})", file=sys.stderr)
    body = {"queries": [{"query": s, "params": []} for s in statements]}
    try:
        out = neon_http(host, body, url=url, read_only=not write, isolation="RepeatableRead")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:500]}")
    return [res["rows"] for res in out["results"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("sql", nargs="+", help="statements, run in order in one transaction")
    ap.add_argument("--write", action="store_true", help="allow writes (default: read-only transaction)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of rows")
    a = ap.parse_args()
    results = run(a.sql, a.write)
    if a.json:
        print(json.dumps(results, indent=1, default=str))
    else:
        for rows in results:
            for row in rows:
                print("\t".join(str(v) for v in row.values()))
            print(f"({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
