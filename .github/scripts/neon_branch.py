"""Create or delete the enrichment's own Neon branch, and print its connection string.

Enrichment must never write to the release database. Layers 1-8 publish there, possibly while
enrichment is mid-run, and a half-finished enrichment must not be visible to anything reading the
release. A branch is copy-on-write, costs nothing to make, and is deleted when the run ends —
so a failed run leaves the release database exactly as it found it.

    python .github/scripts/neon_branch.py create <name>   # prints DATABASE_URL=..., BRANCH_ID=...
    python .github/scripts/neon_branch.py delete <branch_id>

A run may instead target the release database directly (enrich.yml's `target: main`), which is
what publishing an enrichment pass means. That path creates no branch and so has nothing to
delete — `delete` refuses a default branch outright rather than relying on never being handed one.
"""
from __future__ import annotations
import json, os, sys, urllib.error, urllib.request

API = "https://console.neon.tech/api/v2"


def call(path: str, method: str = "GET", body: dict | None = None):
    key = os.environ.get("NEON_API_KEY")
    if not key:
        sys.exit("NEON_API_KEY is not set")
    req = urllib.request.Request(
        f"{API}{path}", method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                 **({"Content-Type": "application/json"} if body else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"neon api {method} {path} -> {e.code}: {e.read().decode('utf-8','replace')[:300]}")


def project_id() -> str:
    pid = os.environ.get("NEON_PROJECT_ID")
    if pid:
        return pid
    # a project-scoped key names its project in the error it returns for a listing
    try:
        return call("/projects")["projects"][0]["id"]
    except SystemExit as e:
        import re
        m = re.search(r'subject_project_id:"([^"]+)"', str(e))
        if m:
            return m.group(1)
        raise


def main() -> int:
    action, arg = sys.argv[1], sys.argv[2]
    pid = project_id()
    if action == "create":
        parent = call(f"/projects/{pid}/branches")["branches"]
        default = next((b["id"] for b in parent if b.get("default")), parent[0]["id"])
        made = call(f"/projects/{pid}/branches", "POST",
                    {"branch": {"name": arg, "parent_id": default},
                     "endpoints": [{"type": "read_write"}]})
        bid = made["branch"]["id"]
        uri = call(f"/projects/{pid}/connection_uri"
                   f"?branch_id={bid}&database_name=neondb&role_name=neondb_owner")["uri"]
        out = os.environ.get("GITHUB_OUTPUT")
        env = os.environ.get("GITHUB_ENV")
        if out:
            with open(out, "a") as fh:
                fh.write(f"branch_id={bid}\n")
        if env:
            with open(env, "a") as fh:
                fh.write(f"DATABASE_URL={uri}\n")
        # the URI is a credential: print the id, never the string
        print(f"created branch {bid} from {default} (connection string written to the environment)")
        return 0
    if action == "delete":
        # The cleanup job deletes whatever branch id it is given. A run targeting main creates no
        # branch and passes an empty id, so this should never fire — which is exactly why it is
        # worth asserting: the failure it prevents is unrecoverable and the check costs one call.
        branch = call(f"/projects/{pid}/branches/{arg}").get("branch", {})
        if branch.get("default"):
            sys.exit(f"refusing to delete {arg}: it is the project's default branch")
        call(f"/projects/{pid}/branches/{arg}", "DELETE")
        print(f"deleted branch {arg}")
        return 0
    return sys.exit(f"unknown action {action!r}")


if __name__ == "__main__":
    raise SystemExit(main())
