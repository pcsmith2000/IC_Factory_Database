"""How the pipeline reaches the Neon warehouse: one place for the URL, the HTTPS endpoint and psycopg.

    resolve_url()            DATABASE_URL_UNPOOLED, else DATABASE_URL (optionally after other names)
    neon_connection_string() what `python -m pipeline.neon_sql` connects with: DATABASE_URL, else the
                             Neon API (NEON_API_KEY + NEON_PROJECT_ID / .neon / the key's project)
    neon_http()              one POST to Neon's SQL-over-HTTPS endpoint (port 5432 is often blocked)
    psycopg_connect()        a psycopg connection with this repo's timeouts and read-only guard

The variable names are the ones the GitHub secrets are mapped to (the workflows fall back to the
IC_DB_* spellings before Python runs); none of them is renamed here.
"""
from __future__ import annotations
import json, os, re, sys, urllib.error, urllib.parse, urllib.request

# UNPOOLED first: DDL and one long transaction per release do not belong on a PgBouncer pool.
URL_VARS = ("DATABASE_URL_UNPOOLED", "DATABASE_URL")
NEON_API = "https://console.neon.tech/api/v2"
LIVE_HOST = "ep-fragrant-snow-awfbmy0k"   # the warehouse the Actions workflows write to


# ---------------------------------------------------------------- the connection string
def check_url(url: str, var: str = "the connection string") -> str:
    """Refuse a connection string with a non-ASCII character in it, naming where.

    The URI is sent as an HTTP header to Neon's endpoint, and headers are latin-1. A connection
    string copied out of a UI that hides the password arrives with a literal '…' in it: it looks
    set, it passes every "is it empty" test, and it failed run 35416196940 in all seven enrichment
    stages as a UnicodeEncodeError 120 frames down in http.client. Saying so here costs one line.
    ASCII rather than latin-1 because a Postgres URL is ASCII (anything else is percent-encoded).
    """
    bad = next((i for i, ch in enumerate(url) if ord(ch) > 127), None)
    if bad is None:
        return url
    raise RuntimeError(
        f"{var} is not usable: it holds a non-ASCII character at position {bad} ({url[bad]!r}), and "
        f"it is sent as an HTTP header. A URL copied from a UI that hides the password looks exactly "
        f"like this — re-copy it with the password shown.")


def resolve(prefer: tuple[str, ...] = (), env=None) -> tuple[str | None, str | None]:
    """(variable, url) for the first of `prefer`, DATABASE_URL_UNPOOLED, DATABASE_URL that is set.

    The variable is returned too because which one won is worth saying out loud: a bad UNPOOLED
    value hides a good DATABASE_URL (run 35393440727). A set-but-unusable value raises rather than
    quietly falling through to the next name — that would connect to a database nobody chose."""
    env = os.environ if env is None else env
    for var in (*prefer, *URL_VARS):
        url = (env.get(var) or "").strip()
        if url:
            return var, check_url(url, var)
    return None, None


def resolve_url(prefer: tuple[str, ...] = (), env=None) -> str | None:
    """The warehouse URL: DATABASE_URL_UNPOOLED or DATABASE_URL (after any `prefer` names)."""
    return resolve(prefer, env)[1]


def neon_host(url: str) -> str:
    """The endpoint host of a postgres:// URL: after the credentials, before any port, path or query."""
    rest = url.split("://", 1)[-1]
    rest = rest.split("@", 1)[1] if "@" in rest else rest
    return re.match(r"[^/:?#]*", rest).group(0)


# ---------------------------------------------------------------- the Neon API
def neon_api(path: str, key: str) -> dict:
    req = urllib.request.Request(NEON_API + path, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def neon_project_id(key: str) -> str:
    if os.environ.get("NEON_PROJECT_ID"):
        return os.environ["NEON_PROJECT_ID"]
    if os.path.exists(".neon"):
        with open(".neon") as f:
            pid = json.load(f).get("projectId")
        if pid:
            return pid
    try:
        projects = neon_api("/projects", key)["projects"]
    except urllib.error.HTTPError as e:   # a project-scoped key names its project in the refusal
        m = re.search(r'subject_project_id:\\?"([^"\\]+)', e.read().decode())
        if m:
            return m.group(1)
        raise
    if len(projects) != 1:
        sys.exit(f"{len(projects)} projects visible to NEON_API_KEY: set NEON_PROJECT_ID")
    return projects[0]["id"]


def neon_connection_string() -> str:
    """DATABASE_URL if set, otherwise the default branch's unpooled URI from the Neon API.

    Only DATABASE_URL, not UNPOOLED: this is the documented resolution of `python -m
    pipeline.neon_sql` (CLAUDE.md), where DATABASE_URL is e.g. a claude_agent role's string."""
    if os.environ.get("DATABASE_URL"):
        return check_url(os.environ["DATABASE_URL"], "DATABASE_URL")
    key = os.environ.get("NEON_API_KEY")
    if not key:
        sys.exit("no DATABASE_URL and no NEON_API_KEY in the environment")
    project = neon_project_id(key)
    branches = neon_api(f"/projects/{project}/branches", key)["branches"]
    branch = next(b["id"] for b in branches if b.get("default"))
    db = neon_api(f"/projects/{project}/branches/{branch}/databases", key)["databases"][0]
    q = urllib.parse.urlencode({"branch_id": branch, "database_name": db["name"], "pooled": "false",
                                "role_name": os.environ.get("NEON_ROLE") or db["owner_name"]})
    return neon_api(f"/projects/{project}/connection_uri?{q}", key)["uri"]


# ---------------------------------------------------------------- Neon's HTTPS SQL endpoint
def neon_http(host: str, body: dict, *, url: str | None = None, read_only: bool | None = None,
              isolation: str | None = None, timeout: int = 120) -> dict:
    """POST one body ({"query", "params"} or {"queries": [...]}) to https://<host>/sql.

    url=None sends no Neon-Connection-String header (a Claude cloud session's egress proxy adds
    it). read_only / isolation set the batch headers only when given, so each caller sends exactly
    the headers it always has. HTTP errors propagate as urllib.error.HTTPError."""
    headers = {"Content-Type": "application/json"}
    if url:
        headers["Neon-Connection-String"] = url
    if isolation:
        headers["Neon-Batch-Isolation-Level"] = isolation
    if read_only is not None:
        headers["Neon-Batch-Read-Only"] = "true" if read_only else "false"
    req = urllib.request.Request(f"https://{host}/sql", method="POST", data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------- psycopg
def psycopg_connect(url: str, *, dict_rows: bool = False, read_only: bool = False,
                    statement_timeout_ms: int | None = None, lock_timeout_ms: int | None = None,
                    connect_timeout: int | None = 20):
    """psycopg.connect with the options this repo uses. read_only=True opens the session with
    default_transaction_read_only and refuses to return a connection that did not take it."""
    import psycopg
    kw: dict = {}
    if connect_timeout is not None:
        kw["connect_timeout"] = connect_timeout
    if dict_rows:
        from psycopg.rows import dict_row
        kw["row_factory"] = dict_row
    opts = []
    if read_only:
        opts.append("-c default_transaction_read_only=on")
    if statement_timeout_ms is not None:
        opts.append(f"-c statement_timeout={statement_timeout_ms}")
    if lock_timeout_ms is not None:
        opts.append(f"-c lock_timeout={lock_timeout_ms}")
    if opts:
        kw["options"] = " ".join(opts)
    conn = psycopg.connect(url, **kw)
    if read_only:
        conn.read_only = True
        row = conn.execute("SHOW transaction_read_only").fetchone()
        if (row["transaction_read_only"] if isinstance(row, dict) else row[0]) != "on":
            conn.close()
            raise RuntimeError("database read-only protection was not enabled")
    return conn
