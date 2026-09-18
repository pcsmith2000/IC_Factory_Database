"""A ledger of calls already made, keyed by what was asked rather than by who asked.

`fact_assertions` already survives an ingestion re-run — nothing in layers 1-8 truncates it, and
stage 13 carries enrichment forward across release tags. So the warehouse is not where enrichment
gets lost. It gets lost when the *question* is asked again under a different name:

  - a facility id changes (the registry churns, a merge splits, resolution improves), so
    `has_rooftop` and `geocode_tried` — which ask "has THIS FACILITY been done?" — both say no;
  - two facilities share an address, and the second pays for a lookup the first already made;
  - the database itself is recreated, and the only record of a year of API calls goes with it.

A geocode is a pure function of an address string. A footprint is a pure function of a coordinate
and an Overture release. A located address is a function of a company name and a city. None of them
depend on facility identity, release tags or golden, so none of them should be cached against those.

Two rules give the table its meaning:

  A POSITIVE result is reusable by anyone. An address is an address, whoever asked.
  A NEGATIVE result is reusable only by the provider that produced it. "qwen found nothing" is a
  fact about qwen, and caching it under the input alone would permanently block a better model
  from ever trying — turning a cache into a ceiling.
"""
from __future__ import annotations
import hashlib, json, re
from datetime import datetime, timezone

DDL = """
CREATE TABLE IF NOT EXISTS cache_lookup (
    cache_key  TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,      -- geocode | footprint | locate
    input      TEXT NOT NULL,      -- the normalised question, readable so the table can be audited
    result     TEXT,               -- JSON; NULL is not used, a miss is an absent row
    found      INTEGER NOT NULL,   -- 1 = the provider answered, 0 = it looked and found nothing
    provider   TEXT NOT NULL,      -- geocodio | overture:<release> | <model>+<search>
    fetched_at TEXT NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0)
"""


def _norm(s: str) -> str:
    """Case, punctuation and spacing carry no meaning for any of these providers."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def key(kind: str, *parts: str) -> str:
    h = hashlib.sha256("\x1f".join([kind, *(_norm(p) for p in parts)]).encode())
    return h.hexdigest()[:32]


def geocode_key(one_line_address: str) -> str:
    return key("geocode", one_line_address)


def footprint_key(lat: float, lon: float, release: str) -> str:
    # ~1e-6 degrees is about 10cm, far finer than a rooftop geocode is accurate, so rounding here
    # costs nothing and stops float formatting drift from missing a cached answer.
    return key("footprint", f"{float(lat):.6f},{float(lon):.6f}", release)


def locate_key(name: str, city: str, state: str) -> str:
    return key("locate", name, city, state)


def get(db, cache_key: str, provider: str | None = None) -> dict | None:
    """The cached answer, or None for a miss.

    `provider` is what makes a negative result safe to store: pass it and a "found nothing" from a
    different provider is treated as a miss, so switching to a better model re-asks the questions
    the cheaper one could not answer, while every positive result is still reused.
    """
    rows = db.query("SELECT result, found, provider FROM cache_lookup WHERE cache_key = $1",
                    (cache_key,))
    if not rows:
        return None
    row = rows[0]
    negative = not int(row["found"] or 0)
    if negative and provider is not None and row["provider"] != provider:
        return None
    db.query("UPDATE cache_lookup SET hits = hits + 1 WHERE cache_key = $1", (cache_key,))
    return {"found": not negative, "provider": row["provider"],
            "result": json.loads(row["result"]) if row["result"] else None}


def get_many(db, keys, provider: str | None = None, chunk: int = 200) -> dict:
    """Every cached answer for `keys`, keyed by cache_key, in one query per chunk.

    get() costs two round trips per key, which is fine for a handful and not for a backlog: asking
    the ledger about 960 facilities one at a time takes longer than the model calls it exists to
    avoid. The provider rule is the same one get() applies — a negative is only reused by the
    provider that produced it, so a better model still gets to try what a cheaper one could not
    answer.
    """
    keys = list(dict.fromkeys(k for k in keys if k))
    out, served = {}, []
    for i in range(0, len(keys), chunk):
        part = keys[i:i + chunk]
        holes = ",".join(f"${n}" for n in range(1, len(part) + 1))
        rows = db.query(f"SELECT cache_key, result, found, provider FROM cache_lookup "
                        f"WHERE cache_key IN ({holes})", tuple(part))
        for row in rows:
            negative = not int(row["found"] or 0)
            if negative and provider is not None and row["provider"] != provider:
                continue
            out[row["cache_key"]] = {"found": not negative, "provider": row["provider"],
                                     "result": json.loads(row["result"]) if row["result"] else None}
            served.append(row["cache_key"])
    for i in range(0, len(served), chunk):
        part = served[i:i + chunk]
        holes = ",".join(f"${n}" for n in range(1, len(part) + 1))
        db.query(f"UPDATE cache_lookup SET hits = hits + 1 WHERE cache_key IN ({holes})",
                 tuple(part))
    return out


PUT = """
INSERT INTO cache_lookup (cache_key, kind, input, result, found, provider, fetched_at, hits)
VALUES ($1,$2,$3,$4,$5,$6,$7,0)
ON CONFLICT (cache_key) DO UPDATE SET
    result = EXCLUDED.result, found = EXCLUDED.found,
    provider = EXCLUDED.provider, fetched_at = EXCLUDED.fetched_at
"""


def put(db, cache_key: str, kind: str, input_text: str, result, found: bool, provider: str) -> None:
    db.query(PUT, (cache_key, kind, input_text[:500],
                   json.dumps(result, default=str) if result is not None else None,
                   1 if found else 0, provider,
                   datetime.now(timezone.utc).isoformat(timespec="seconds")))


def ensure(db) -> None:
    db.query(DDL, ())


def stats(db) -> list[dict]:
    return db.query("""SELECT kind, provider, COUNT(*) AS entries,
                              SUM(found) AS positives, SUM(hits) AS calls_saved
                       FROM cache_lookup GROUP BY 1, 2 ORDER BY 3 DESC""", ())


def dump(db) -> list[dict]:
    """The whole ledger, for keeping somewhere the database cannot take with it when it goes."""
    return db.query("SELECT cache_key, kind, input, result, found, provider, fetched_at "
                    "FROM cache_lookup ORDER BY fetched_at", ())


def restore(db, rows: list[dict], chunk: int = 200) -> int:
    ensure(db)
    n = 0
    for i in range(0, len(rows), chunk):
        for r in rows[i:i + chunk]:
            db.query(PUT, (r["cache_key"], r["kind"], r["input"], r["result"],
                           int(r["found"]), r["provider"], r["fetched_at"]))
            n += 1
    return n
