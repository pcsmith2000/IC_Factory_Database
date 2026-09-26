"""Load a demand pass (projects.json) into the warehouse's demand tables. Dry run unless --write.

Three tables, all append-only from here:

  demand_project      one row per project, DP-000001 onwards, never reissued. review_status starts
                      'proposed': nothing the pipeline finds is confirmed until a person says so.
  demand_project_key  the names (and name + state) a project has been found under, so a later pass
                      that finds it again adds evidence to the same project instead of a new one
  demand_assertion    one row per (project, field, value, page): the value, the verbatim quote that
                      states it, the URL, when it was fetched, and the run and config that found it.
                      Conflicting values are all kept; nothing is overwritten. The field `supplier`
                      is the tie to the manufacturer (the quote naming it); its facility_ids are the
                      manufacturer's plants in golden, not a claim about which plant built it.

    python -m pipeline.demand.load --pass pass/ --run-id 123 [--write]

The connection comes from DATABASE_URL (psycopg), otherwise Neon's HTTPS endpoint (pipeline/neon_sql.py).
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import norm

DDL = [
    """CREATE TABLE IF NOT EXISTS demand_project (
        project_id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT, country TEXT,
        review_status TEXT NOT NULL DEFAULT 'proposed'
            CHECK (review_status IN ('proposed', 'confirmed', 'rejected', 'merged')),
        merged_into TEXT REFERENCES demand_project (project_id),
        created_at TEXT NOT NULL, created_by TEXT NOT NULL,
        reviewed_at TEXT, reviewed_by TEXT, review_note TEXT)""",
    """CREATE TABLE IF NOT EXISTS demand_project_key (
        match_key TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES demand_project (project_id),
        first_seen TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS demand_assertion (
        assertion_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES demand_project (project_id),
        field TEXT NOT NULL, value TEXT NOT NULL, quote TEXT NOT NULL, url TEXT NOT NULL,
        source_kind TEXT NOT NULL, retrieved_at TEXT, manufacturer TEXT, facility_ids TEXT,
        run_id TEXT NOT NULL, config TEXT, model TEXT, asserted_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_demand_assertion_project ON demand_assertion (project_id, field)",
]


def project_keys(p: dict) -> list[str]:
    """Every name the project was found under, alone and with its state."""
    out = []
    for name in [p["name"]] + list(p.get("aliases") or []):
        n = norm(re.sub(r"\b(the|apartments?|residences?|project)\b", "", str(name).lower()))
        if len(n) >= 4:
            out.append(f"name:{n}|{p.get('state') or ''}")
    for e in (p["fields"].get("address") or []):
        if p.get("state") and norm(e["value"]):
            out.append(f"addr:{norm(e['value'])}|{p['state']}")
    return list(dict.fromkeys(out))


def assertion_id(project_id: str, field: str, value, url: str, quote: str) -> str:
    return hashlib.sha256("\x1f".join(map(str, (project_id, field, value, url, quote))).encode()).hexdigest()[:32]


def plan(doc: dict, existing_keys: dict[str, str], next_number: int, run_id: str, now: str) -> dict:
    """What a load would write, given the keys already in the warehouse. Pure: tests call it directly."""
    s = doc["summary"]
    model = None
    projects, keys, assertions, matched = [], [], [], []
    seen_keys = dict(existing_keys)
    for p in doc["projects"]:
        ks = project_keys(p)
        pid = next((seen_keys[k] for k in ks if k in seen_keys), None)
        if pid:
            matched.append({"project_id": pid, "name": p["name"]})
        else:
            pid = f"DP-{next_number:06d}"; next_number += 1
            country = (p["fields"].get("country") or [{}])[0].get("value")
            projects.append({"project_id": pid, "name": p["name"], "state": p.get("state"), "country": country,
                             "created_at": now, "created_by": run_id})
        for k in ks:
            if k not in seen_keys:
                seen_keys[k] = pid
                keys.append({"match_key": k, "project_id": pid, "first_seen": now})
        base = {"project_id": pid, "manufacturer": p["manufacturer"], "facility_ids": json.dumps(p.get("facility_ids") or []),
                "run_id": run_id, "config": s.get("config"), "model": model, "asserted_at": now}
        for field, es in p["fields"].items():
            for e in es:
                assertions.append(dict(base, assertion_id=assertion_id(pid, field, e["value"], e["url"], e["quote"]),
                                       field=field, value=str(e["value"]), quote=e["quote"], url=e["url"],
                                       source_kind=e["source_kind"], retrieved_at=e.get("fetched_at")))
        for t in p.get("ties") or []:
            assertions.append(dict(base, assertion_id=assertion_id(pid, "supplier", p["company"], t["url"], t["quote"]),
                                   field="supplier", value=p["company"], quote=t["quote"] or "", url=t["url"],
                                   source_kind=t["source_kind"], retrieved_at=None))
    uniq = {a["assertion_id"]: a for a in assertions}
    return {"projects": projects, "keys": keys, "assertions": list(uniq.values()), "matched": matched}


PROJECT_COLS = ("project_id", "name", "state", "country", "created_at", "created_by")
KEY_COLS = ("match_key", "project_id", "first_seen")
ASSERTION_COLS = ("assertion_id", "project_id", "field", "value", "quote", "url", "source_kind", "retrieved_at",
                  "manufacturer", "facility_ids", "run_id", "config", "model", "asserted_at")


def statements(p: dict) -> list[tuple[str, list]]:
    out = []
    for table, cols, rows, pk in (("demand_project", PROJECT_COLS, p["projects"], "project_id"),
                                  ("demand_project_key", KEY_COLS, p["keys"], "match_key"),
                                  ("demand_assertion", ASSERTION_COLS, p["assertions"], "assertion_id")):
        for r in rows:
            out.append((f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                        f"ON CONFLICT ({pk}) DO NOTHING", [r.get(c) for c in cols]))
    return out


# --- connections ------------------------------------------------------------------------------------

class Psycopg:
    def __init__(self, url: str):
        import psycopg
        self.db = psycopg.connect(url, connect_timeout=20)

    def query(self, sql: str) -> list[tuple]:
        return self.db.execute(sql).fetchall()

    def write(self, stmts: list[tuple[str, list]]):
        with self.db.transaction():
            for sql, params in stmts:
                self.db.execute(sql, params)


class NeonHttp:
    """Neon's HTTPS SQL endpoint, one transaction per call (pipeline/neon_sql.py resolves the connection)."""
    def __init__(self):
        from .. import neon_sql
        if os.environ.get("DATABASE_URL") or os.environ.get("NEON_API_KEY"):
            self.url = neon_sql.connection_string()
            self.host = re.search(r"@([^/:?]+)", self.url).group(1)
        else:                                            # a Claude cloud session: the egress proxy adds the header
            self.url, self.host = None, f"{neon_sql.LIVE_HOST}.c-12.us-east-1.aws.neon.tech"

    def _post(self, queries: list[dict], read_only: bool):
        import urllib.request
        body = json.dumps({"queries": queries} if len(queries) > 1 else queries[0]).encode()
        headers = {"Content-Type": "application/json", "Neon-Batch-Read-Only": "true" if read_only else "false"}
        if self.url:
            headers["Neon-Connection-String"] = self.url
        host = self.host
        req = urllib.request.Request(f"https://{host}/sql", data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)

    def query(self, sql: str) -> list[tuple]:
        res = self._post([{"query": sql, "params": []}], True)
        if "rows" not in res:
            raise RuntimeError(res.get("message") or "query failed")
        return [tuple(row.values()) for row in res["rows"]]

    def write(self, stmts: list[tuple[str, list]]):
        qs = []
        for sql, params in stmts:
            n = iter(range(1, len(params) + 1))
            qs.append({"query": re.sub(r"%s", lambda _: f"${next(n)}", sql), "params": params})
        for i in range(0, len(qs), 200):
            self._post(qs[i:i + 200], False)


def connect():
    url = os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    return Psycopg(url) if url else NeonHttp()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pass", dest="pass_dir", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--write", action="store_true", help="write to the warehouse (default: print the plan)")
    a = ap.parse_args(argv)
    doc = json.loads((Path(a.pass_dir) / "projects.json").read_text())
    db = connect()
    if a.write:
        db.write([(s, []) for s in DDL])
    try:
        existing = {k: pid for k, pid in db.query("SELECT match_key, project_id FROM demand_project_key")}
        top = db.query("SELECT max(project_id) FROM demand_project")[0][0]
    except Exception:                                    # tables not created yet (a dry run before the first write)
        existing, top = {}, None
    nxt = int(top.split("-")[1]) + 1 if top else 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    p = plan(doc, existing, nxt, f"demand-{a.run_id}", now)
    print(json.dumps({"new_projects": len(p["projects"]), "matched_existing": len(p["matched"]),
                      "new_keys": len(p["keys"]), "assertions": len(p["assertions"]), "write": a.write}, indent=1))
    if a.write:
        db.write(statements(p))
        print("written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
