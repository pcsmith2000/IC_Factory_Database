"""A warehouse that will not connect must be a recorded halt, not a traceback.

Run 35393440727 classified 167 batches, reconciled, passed every gate, measured 42.7% recall — and
then died on `psycopg.OperationalError: password authentication failed` as a bare traceback. No run
record, no failed heartbeat. The measurements survived only because the artifact upload runs on
always(). Layer 8 is the last step; by the time it runs, the record is the whole point.
"""
import pytest
from pipeline import warehouse as W


def test_a_refused_connection_is_WarehouseUnreachable_and_names_the_variable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://u:wrong@h.example/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:right@h-pooler.example/db")

    class Boom(Exception): pass
    def refuse(url, **kw):
        raise Boom("password authentication failed for user 'neondb_owner'\nsecond line dropped")
    monkeypatch.setattr(W.PostgresWarehouse, "__init__",
                        lambda self, url: (_ for _ in ()).throw(Boom("password authentication failed")))

    with pytest.raises(W.WarehouseUnreachable) as e:
        W.open_warehouse({"warehouse": {"engine": "postgres"}}, __import__("pathlib").Path("."))
    msg = str(e.value)
    assert "DATABASE_URL_UNPOOLED" in msg              # the variable actually in use
    assert "read BEFORE DATABASE_URL" in msg           # and why the good one never got a turn
    assert "wrong" not in msg and "***" in msg         # the password is redacted, not printed


def test_the_pooled_variable_is_named_when_it_is_the_one_in_use(monkeypatch):
    monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h.example/db")
    monkeypatch.setattr(W.PostgresWarehouse, "__init__",
                        lambda self, url: (_ for _ in ()).throw(RuntimeError("nope")))
    with pytest.raises(W.WarehouseUnreachable) as e:
        W.open_warehouse({"warehouse": {"engine": "postgres"}}, __import__("pathlib").Path("."))
    assert str(e.value).startswith("DATABASE_URL did not connect")


def test_a_missing_driver_is_still_NotImplemented_not_Unreachable(monkeypatch):
    """The two are different problems: one is a deploy fault, the other a secret to correct."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h.example/db")
    monkeypatch.setattr(W.PostgresWarehouse, "__init__",
                        lambda self, url: (_ for _ in ()).throw(W.WarehouseNotImplemented("no psycopg")))
    with pytest.raises(W.WarehouseNotImplemented):
        W.open_warehouse({"warehouse": {"engine": "postgres"}}, __import__("pathlib").Path("."))
