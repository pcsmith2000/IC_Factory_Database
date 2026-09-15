"""Resolve DATABASE_URL / DATABASE_URL_UNPOOLED for the run from the Neon GitHub integration.

The integration stores NEON_API_KEY (secret) and NEON_PROJECT_ID (variable); it does not store a
connection string. When DATABASE_URL is already set (a repository secret) this script does nothing.
Otherwise it asks the Neon API for the project's default branch, that branch's first database and
its owner role, and writes both connection URIs to $GITHUB_ENV. Any missing piece is reported and
the step fails — Layer 8 then halts loudly rather than loading nothing.

Env in:  NEON_API_KEY, NEON_PROJECT_ID, optional NEON_BRANCH_ID, NEON_DATABASE, NEON_ROLE
Env out: DATABASE_URL (pooled), DATABASE_URL_UNPOOLED (direct)  →  $GITHUB_ENV
"""
import json, os, sys, urllib.parse, urllib.request

API = "https://console.neon.tech/api/v2"


def get(path: str, key: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> int:
    if os.environ.get("DATABASE_URL"):
        print("DATABASE_URL already set — using the repository secret"); return 0
    key, project = os.environ.get("NEON_API_KEY"), os.environ.get("NEON_PROJECT_ID")
    if not (key and project):
        print("no DATABASE_URL secret and no NEON_API_KEY/NEON_PROJECT_ID from the Neon GitHub integration — warehouse will use sqlite (workflow artifact only)")
        return 0
    branch = os.environ.get("NEON_BRANCH_ID")
    if not branch:
        branches = get(f"/projects/{project}/branches", key)["branches"]
        branch = next((b["id"] for b in branches if b.get("default")), branches[0]["id"])
    dbs = get(f"/projects/{project}/branches/{branch}/databases", key)["databases"]
    if not dbs:
        print(f"branch {branch} has no databases", file=sys.stderr); return 1
    db = next((d for d in dbs if d["name"] == os.environ.get("NEON_DATABASE")), dbs[0])
    role = os.environ.get("NEON_ROLE") or db["owner_name"]
    out = {}
    for var, pooled in (("DATABASE_URL", "true"), ("DATABASE_URL_UNPOOLED", "false")):
        q = urllib.parse.urlencode({"branch_id": branch, "database_name": db["name"], "role_name": role, "pooled": pooled})
        out[var] = get(f"/projects/{project}/connection_uri?{q}", key)["uri"]
    gh_env = os.environ.get("GITHUB_ENV")
    if gh_env:
        with open(gh_env, "a") as f:
            for k, v in out.items():
                f.write(f"{k}={v}\n")
    print(f"resolved connection strings for project {project}, branch {branch}, database {db['name']}, role {role} (values masked)")
    print("::add-mask::" + out["DATABASE_URL"]); print("::add-mask::" + out["DATABASE_URL_UNPOOLED"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
