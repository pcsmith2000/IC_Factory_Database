"""pipeline/neon_sql.py and the connection helpers under it (pipeline/db.py), with the HTTP layer
mocked: nothing here reaches Neon."""
import io
import json
import urllib.error
import urllib.request

import pytest

from pipeline import db, neon_sql

LIVE = "postgresql://agent:pw@ep-fragrant-snow-awfbmy0k.c-12.us-east-1.aws.neon.tech/neondb?sslmode=require"


class Reply(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("DATABASE_URL", "DATABASE_URL_UNPOOLED", "NEON_API_KEY", "NEON_PROJECT_ID", "NEON_ROLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)                       # no stray .neon file
    return monkeypatch


@pytest.fixture
def http(env):
    """Records every request; answers from `replies` (a callable on the request, or a dict)."""
    sent, replies = [], []

    def urlopen(req, timeout=None):
        sent.append(req)
        out = replies.pop(0)
        out = out(req) if callable(out) else out
        if isinstance(out, Exception):
            raise out
        return Reply(json.dumps(out).encode())
    env.setattr(urllib.request, "urlopen", urlopen)
    return sent, replies


def _headers(req):
    return {k.lower(): v for k, v in req.header_items()}


def test_a_read_runs_every_statement_in_one_read_only_batch(env, http):
    sent, replies = http
    env.setenv("DATABASE_URL", LIVE)
    replies.append({"results": [{"rows": [{"n": 3}]}, {"rows": []}]})
    assert neon_sql.run(["SELECT 1", "SELECT 2"]) == [[{"n": 3}], []]
    (req,) = sent
    assert req.full_url == "https://ep-fragrant-snow-awfbmy0k.c-12.us-east-1.aws.neon.tech/sql"
    assert req.get_method() == "POST"
    h = _headers(req)
    assert h["neon-connection-string"] == LIVE
    assert h["neon-batch-read-only"] == "true"
    assert h["neon-batch-isolation-level"] == "RepeatableRead"
    assert json.loads(req.data) == {"queries": [{"query": "SELECT 1", "params": []},
                                                {"query": "SELECT 2", "params": []}]}


def test_write_lifts_the_read_only_header_only_when_asked(env, http):
    sent, replies = http
    env.setenv("DATABASE_URL", LIVE)
    replies.append({"results": [{"rows": []}]})
    neon_sql.run(["UPDATE x SET y = 1"], write=True)
    assert _headers(sent[0])["neon-batch-read-only"] == "false"


def test_another_host_is_warned_about(env, http, capsys):
    _, replies = http
    env.setenv("DATABASE_URL", "postgresql://u:p@ep-other.aws.neon.tech/neondb")
    replies.append({"results": [{"rows": []}]})
    neon_sql.run(["SELECT 1"])
    assert "ep-other.aws.neon.tech is not the live warehouse" in capsys.readouterr().err


def test_an_http_error_exits_with_its_status_and_body(env, http):
    _, replies = http
    env.setenv("DATABASE_URL", LIVE)
    replies.append(urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b'{"message":"syntax error"}')))
    with pytest.raises(SystemExit, match=r'HTTP 400: \{"message":"syntax error"\}'):
        neon_sql.run(["SELEC 1"])


def test_without_database_url_the_api_key_resolves_the_default_branch_owner(env, http):
    sent, replies = http
    env.setenv("NEON_API_KEY", "key")
    env.setenv("NEON_PROJECT_ID", "proj")
    replies += [{"branches": [{"id": "br-old"}, {"id": "br-main", "default": True}]},
                {"databases": [{"name": "neondb", "owner_name": "neondb_owner"}]},
                {"uri": LIVE}]
    assert db.neon_connection_string() == LIVE
    paths = [r.full_url.removeprefix(db.NEON_API) for r in sent]
    assert paths[:2] == ["/projects/proj/branches", "/projects/proj/branches/br-main/databases"]
    assert paths[2].startswith("/projects/proj/connection_uri?")
    assert "branch_id=br-main" in paths[2] and "role_name=neondb_owner" in paths[2] and "pooled=false" in paths[2]
    assert all(_headers(r)["authorization"] == "Bearer key" for r in sent)


def test_neon_role_overrides_the_owner(env, http):
    sent, replies = http
    env.setenv("NEON_API_KEY", "key"); env.setenv("NEON_PROJECT_ID", "proj"); env.setenv("NEON_ROLE", "claude_agent")
    replies += [{"branches": [{"id": "b", "default": True}]},
                {"databases": [{"name": "neondb", "owner_name": "neondb_owner"}]}, {"uri": LIVE}]
    db.neon_connection_string()
    assert "role_name=claude_agent" in sent[2].full_url


def test_a_project_scoped_key_names_its_project_in_the_refusal(env, http):
    sent, replies = http
    env.setenv("NEON_API_KEY", "key")
    body = b'{"message":"not allowed: subject_project_id:\\"proj-scoped\\""}'
    replies.append(urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(body)))
    assert db.neon_project_id("key") == "proj-scoped"


def test_the_git_ignored_neon_file_names_the_project(env, http, tmp_path):
    (tmp_path / ".neon").write_text(json.dumps({"projectId": "from-file"}))
    assert db.neon_project_id("key") == "from-file"
    assert http[0] == []                              # no API call needed


def test_neither_url_nor_key_exits(env):
    with pytest.raises(SystemExit, match="no DATABASE_URL and no NEON_API_KEY"):
        neon_sql.connection_string()


def test_a_url_with_an_elided_password_is_refused_before_it_is_sent(env, http):
    env.setenv("DATABASE_URL", "postgresql://neondb_owner:…@ep-x.aws.neon.tech/neondb")
    with pytest.raises(RuntimeError, match="DATABASE_URL .*position 26"):
        neon_sql.run(["SELECT 1"])
    assert http[0] == []


def test_the_cli_prints_rows_then_a_count(env, http, capsys, monkeypatch):
    _, replies = http
    env.setenv("DATABASE_URL", LIVE)
    replies.append({"results": [{"rows": [{"state": "OH", "n": 2}, {"state": "PA", "n": 1}]}]})
    monkeypatch.setattr("sys.argv", ["neon_sql", "SELECT state, count(*) AS n FROM golden_facility GROUP BY 1"])
    assert neon_sql.main() == 0
    assert capsys.readouterr().out == "OH\t2\nPA\t1\n(2 rows)\n"


def test_the_cli_prints_json(env, http, capsys, monkeypatch):
    sent, replies = http
    env.setenv("DATABASE_URL", LIVE)
    replies.append({"results": [{"rows": [{"n": 1}]}, {"rows": [{"m": 2}]}]})
    monkeypatch.setattr("sys.argv", ["neon_sql", "--json", "SELECT 1 AS n", "SELECT 2 AS m"])
    assert neon_sql.main() == 0
    assert json.loads(capsys.readouterr().out) == [[{"n": 1}], [{"m": 2}]]
    assert _headers(sent[0])["neon-batch-read-only"] == "true"


# ---------------------------------------------------------------- pipeline/db.py
def test_resolve_prefers_unpooled_and_says_which(env):
    env.setenv("DATABASE_URL", "postgresql://u:p@pooled/db")
    assert db.resolve() == ("DATABASE_URL", "postgresql://u:p@pooled/db")
    env.setenv("DATABASE_URL_UNPOOLED", "postgresql://u:p@direct/db")
    assert db.resolve() == ("DATABASE_URL_UNPOOLED", "postgresql://u:p@direct/db")
    env.setenv("PIPELINE_REPORT_DATABASE_URL", "postgresql://u:p@report/db")
    assert db.resolve_url(("PIPELINE_REPORT_DATABASE_URL",)) == "postgresql://u:p@report/db"


def test_resolve_refuses_a_bad_url_rather_than_falling_through(env):
    env.setenv("DATABASE_URL_UNPOOLED", "postgresql://u:…@direct/db")
    env.setenv("DATABASE_URL", "postgresql://u:p@pooled/db")
    with pytest.raises(RuntimeError, match="DATABASE_URL_UNPOOLED is not usable"):
        db.resolve_url()


def test_resolve_with_nothing_set_is_none(env):
    assert db.resolve() == (None, None)


@pytest.mark.parametrize("url,host", [
    ("postgresql://u:p@ep-x.aws.neon.tech/neondb?sslmode=require", "ep-x.aws.neon.tech"),
    ("postgresql://u:p@ep-x.aws.neon.tech:5432/neondb", "ep-x.aws.neon.tech"),
    ("postgres://ep-x.aws.neon.tech/neondb", "ep-x.aws.neon.tech"),
])
def test_neon_host(url, host):
    assert db.neon_host(url) == host
