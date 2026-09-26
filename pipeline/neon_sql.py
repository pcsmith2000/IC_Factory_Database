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
import argparse, json, os, re, sys, urllib.error, urllib.parse, urllib.request

API = "https://console.neon.tech/api/v2"
LIVE_HOST = "ep-fragrant-snow-awfbmy0k"   # the warehouse the Actions workflows write to


def _api(path: str, key: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _project(key: str) -> str:
    if os.environ.get("NEON_PROJECT_ID"):
        return os.environ["NEON_PROJECT_ID"]
    if os.path.exists(".neon"):
        with open(".neon") as f:
            pid = json.load(f).get("projectId")
        if pid:
            return pid
    try:
        projects = _api("/projects", key)["projects"]
    except urllib.error.HTTPError as e:   # a project-scoped key names its project in the refusal
        m = re.search(r'subject_project_id:\\?"([^"\\]+)', e.read().decode())
        if m:
            return m.group(1)
        raise
    if len(projects) != 1:
        sys.exit(f"{len(projects)} projects visible to NEON_API_KEY: set NEON_PROJECT_ID")
    return projects[0]["id"]


def connection_string() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    key = os.environ.get("NEON_API_KEY")
    if not key:
        sys.exit("no DATABASE_URL and no NEON_API_KEY in the environment")
    project = _project(key)
    branches = _api(f"/projects/{project}/branches", key)["branches"]
    branch = next(b["id"] for b in branches if b.get("default"))
    db = _api(f"/projects/{project}/branches/{branch}/databases", key)["databases"][0]
    q = urllib.parse.urlencode({"branch_id": branch, "database_name": db["name"], "pooled": "false",
                                "role_name": os.environ.get("NEON_ROLE") or db["owner_name"]})
    return _api(f"/projects/{project}/connection_uri?{q}", key)["uri"]


def run(statements: list[str], write: bool = False) -> list[list[dict]]:
    url = connection_string()
    host = urllib.parse.urlparse(url).hostname
    if LIVE_HOST not in host:
        print(f"warning: {host} is not the live warehouse ({LIVE_HOST})", file=sys.stderr)
    body = {"queries": [{"query": s, "params": []} for s in statements]}
    headers = {"Neon-Connection-String": url, "Content-Type": "application/json",
               "Neon-Batch-Isolation-Level": "RepeatableRead", "Neon-Batch-Read-Only": "false" if write else "true"}
    req = urllib.request.Request(f"https://{host}/sql", method="POST", data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return [res["rows"] for res in json.load(r)["results"]]
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:500]}")


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
