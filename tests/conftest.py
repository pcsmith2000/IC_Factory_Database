"""The test warehouse: Postgres at TEST_DATABASE_URL, a fresh schema per test.

Each test that asks for `new_wh` gets warehouses on a schema of its own (search_path set on the
connection), so tests never see each other's rows and nothing has to be deleted between them.
Without TEST_DATABASE_URL these tests skip; CI runs a Postgres service (ci.yml)."""
import os
import uuid

import pytest

PG_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def pg_url():
    """A URL whose connections live in a new, empty schema, dropped after the test."""
    if not PG_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    import psycopg
    schema = "t_" + uuid.uuid4().hex[:12]
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {schema}")
    yield f"{PG_URL}{'&' if '?' in PG_URL else '?'}options=-csearch_path%3D{schema}"
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def new_wh(pg_url):
    """Open a warehouse on this test's schema; every one opened is closed afterwards."""
    from pipeline import warehouse
    opened = []

    def make():
        w = warehouse.PostgresWarehouse(pg_url)
        opened.append(w)
        return w
    yield make
    for w in opened:
        w.close()
