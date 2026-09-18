"""Reading the post-layer-8 release and writing enrichment back as assertions.

Enrichment never writes golden_facility. Each stage appends to fact_assertions under its own
source id, and golden.build_golden decides afterwards, so an inferred coordinate is ranked by
survivorship exactly as a state licence is — and operator corrections still outrank all of it.

Ranking needs no new machinery: golden._rank already understands `basis:<x>`, so a rooftop
geocode asserts basis="rooftop" and survivorship.yaml orders `basis:rooftop` where it belongs.
"""
from __future__ import annotations
import hashlib, random
from datetime import date

# `has_rooftop` is the distinction stage 11 turns on, and it is not the same as "has a coordinate".
# Every coordinate in the release today is asserted by epa_frs, and EPA's are facility-self-reported:
# measured against Overture, 44% of them land within 30m of any building at all and the median
# footprint of those is 4,583 sqft — small incidental structures, not plants. A rooftop geocode on
# the same measurement resolved 18 of 19 with a median of 63,968 sqft. So a coordinate is only
# worth measuring when a rooftop geocode produced it.
SELECT_GOLDEN = """
    SELECT COALESCE(d.facility_id, g.facility_key) AS facility_id, g.facility_key,
           g.name, g.address, g.city, g.state, g.zip, g.lat_lon, g.status, g.expiry_date,
           g.naics,
           d.tier, g.release_tag,
           EXISTS (SELECT 1 FROM fact_assertions a
                    WHERE a.facility_key = g.facility_key
                      AND a.field_key = 'lat_lon' AND a.basis = 'rooftop') AS has_rooftop,
           EXISTS (SELECT 1 FROM fact_assertions a
                    WHERE a.facility_key = g.facility_key
                      AND a.field_key = 'geocode_quality') AS geocode_tried
    FROM golden_facility g
    LEFT JOIN dim_facility d ON d.facility_key = g.facility_key
"""


def snapshot(wh, sample: int | None = None, seed: int = 20260917) -> list[dict]:
    """The release as layer 8 left it. `sample` takes a deterministic, state-stratified subset so
    a design iteration costs a few dozen API calls instead of a few thousand — and so that a
    layers 1-8 run landing mid-iteration cannot move the ground under a comparison."""
    rows = wh.query(SELECT_GOLDEN)
    if not sample or sample >= len(rows):
        return rows
    rng = random.Random(seed)
    by_state: dict[str, list[dict]] = {}
    for r in rows:
        by_state.setdefault((r.get("state") or "??").upper(), []).append(r)
    for v in by_state.values():
        v.sort(key=lambda r: r["facility_id"])       # stable before shuffling
        rng.shuffle(v)
    picked, states = [], sorted(by_state)
    while len(picked) < sample:
        progressed = False
        for s in states:
            if by_state[s] and len(picked) < sample:
                picked.append(by_state[s].pop()); progressed = True
        if not progressed:
            break
    return picked


def needs(rows: list[dict], field: str) -> list[dict]:
    """Facilities missing the field a stage produces. This is what makes stages idempotent and
    lets a facility enter the chain at whatever stage its evidence has reached."""
    return [r for r in rows if not (r.get(field) or "").strip()]


def assertion(facility_id: str, field: str, value: str, *, source_id: str,
              basis: str = "none", confidence: float | None = None,
              evidence: str = "") -> dict:
    """One enrichment assertion, shaped like golden.assertions_from_rows output.

    row_hash anchors provenance. A published source hashes its contract row; enrichment has no
    contract row, so it hashes what actually produced the value — the stage, the facility, and
    the evidence it cited. Re-running a stage on unchanged evidence yields the same hash, which
    is what keeps the append-only table from growing a duplicate on every run.
    """
    h = hashlib.sha256("\x1f".join([source_id, facility_id, field, value, evidence]).encode())
    return {"facility_id": facility_id, "source_id": source_id, "source_class": "enrichment",
            "retrieved_date": date.today().isoformat(), "row_hash": h.hexdigest()[:16],
            "basis": basis, "site_visit": False,
            "confidence": "" if confidence is None else confidence,
            "field": field, "value": value, "evidence": evidence}


# ---------------------------------------------------------------- connecting
class NeonHttp:
    """Neon's SQL-over-HTTPS endpoint, exposing the slice of the warehouse interface the stages use.

    The pooled Postgres port is not reachable from every runner or sandbox, but 443 always is, and
    Neon serves the same database over it. Having one connect() that falls back to HTTP means the
    stages are exercised by the same code path in CI and on a laptop.
    """
    engine = "neon_http"

    def __init__(self, uri: str):
        self.uri = uri
        self.host = uri.split("@", 1)[1].split("/", 1)[0]

    def query(self, sql: str, params=()) -> list[dict]:
        import json, urllib.request
        body = json.dumps({"query": sql, "params": list(params)}).encode()
        req = urllib.request.Request(f"https://{self.host}/sql", data=body, method="POST",
                                     headers={"Neon-Connection-String": self.uri,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.loads(r.read())
        if "rows" not in out:
            raise RuntimeError(f"neon http: {str(out)[:300]}")
        self.last_row_count = out.get("rowCount", 0)
        return out["rows"]

    def executemany(self, sql: str, rows: list[tuple]) -> int:
        for r in rows:
            self.query(sql, r)
        return len(rows)


def connect(url: str | None = None):
    """The enrichment database, always over Neon's HTTPS endpoint.

    Earlier this preferred psycopg and fell back to HTTPS. That fallback was the only branch that
    ever ran — 5432 is not reachable from the sandbox, and the statements below are written with
    Postgres' numbered placeholders, which the warehouse cursor (which rewrites `?`) would not
    translate. One path means the stages are exercised the same way in CI and on a laptop, which
    is what the fallback was there to achieve and did not.
    """
    import os
    url = url or os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("no DATABASE_URL / DATABASE_URL_UNPOOLED for the enrichment database")
    return NeonHttp(url)


# fact_assertions has no evidence column — by design: a published source's assertion points at
# ref_source_row via row_hash, and v_provenance walks that chain to answer "who says so". An
# enrichment assertion has no contract row, so without a ref_source_row entry its citation would be
# checked by gate E1 and then discarded, and the database could never answer why an address was
# believed. Writing one puts an inferred value on exactly the same provenance footing as a state
# licence: source_url is the page the model read, source_document the sentence it read there.
APPEND_EVIDENCE = """
INSERT INTO ref_source_row
  (row_hash, source_key, source_url, source_document, retrieved_date,
   facility_key, match_method, match_confidence, last_seen_release)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
ON CONFLICT (row_hash) DO UPDATE SET last_seen_release = EXCLUDED.last_seen_release
"""

APPEND_ASSERTION = """
INSERT INTO fact_assertions
  (assertion_id, release_tag, facility_key, source_key, field_key, date_key,
   value, basis, site_visit, row_hash, confidence, source_class)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
ON CONFLICT (assertion_id, release_tag) DO NOTHING
"""


def _rows_for(a: dict, release_tag: str, today: str, now: str) -> tuple[tuple, tuple]:
    """The ref_source_row and fact_assertions tuples for one assertion.

    source_url means a URL. A located address cites a page, so it has one; a footprint cites an
    Overture release and building id and a geocode cites a parcel dataset, and neither is a URL.
    Putting those in source_url would make the column mean "whatever the evidence was", and
    anything reading it as a link would be wrong.
    """
    ev = (a.get("evidence") or "")
    url, quote = (ev.partition(" :: ")[0], ev.partition(" :: ")[2]) if ev.startswith("http") else ("", ev)
    when = a.get("retrieved_date") or today
    conf = None if a.get("confidence") in ("", None) else float(a["confidence"])
    aid = f"{a['source_id']}|{a['facility_id']}|{a['field']}|{a['row_hash']}"
    return ((a["row_hash"], a["source_id"], url, quote, when, a["facility_id"],
             a.get("basis", "none"), conf, release_tag),
            (aid, release_tag, a["facility_id"], a["source_id"], a["field"], when,
             a["value"], a.get("basis", "none"), 0, a["row_hash"], conf,
             a.get("source_class", "enrichment"), now))


def _multi(sql_head: str, tail: str, rows: list[tuple]) -> tuple[str, list]:
    """One INSERT ... VALUES (..),(..) with numbered placeholders, plus its flat parameter list."""
    width, params, groups, n = len(rows[0]), [], [], 0
    for r in rows:
        groups.append("(" + ",".join(f"${n + j + 1}" for j in range(width)) + ")")
        params.extend(r)
        n += width
    return f"{sql_head} VALUES {','.join(groups)} {tail}", params


def append(db, assertions: list[dict], release_tag: str, chunk: int = 250) -> dict:
    """Append enrichment assertions. ON CONFLICT DO NOTHING plus the evidence-derived row_hash is
    what makes a re-run a no-op rather than a duplicate.

    Batched for the same reason the golden rebuild is: NeonHttp does one HTTPS round trip per
    query(), and a row at a time meant two per assertion. A 2,000 assertion geocode run is 4,000
    requests that way, which is minutes of latency and nothing else.

    Reports rows actually inserted, not rows offered. A second run over unchanged evidence offers
    the same assertions and inserts none of them, and a summary that called that "2,000 appended"
    would be reporting the opposite of the property the design depends on.
    """
    from datetime import date, datetime, timezone
    today = date.today().isoformat()
    # One timestamp for the whole append: every assertion of a run is equally recent, and a run
    # that re-measures a facility must sort strictly after the run that measured it before.
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # The only writer of enrichment assertions, so the only place the column has to be guaranteed.
    add_assertion_columns(db, missing_assertion_columns(db))
    inserted = 0
    for i in range(0, len(assertions), chunk):
        batch = [_rows_for(a, release_tag, today, now) for a in assertions[i:i + chunk]]
        sql, params = _multi(
            "INSERT INTO ref_source_row (row_hash, source_key, source_url, source_document,"
            " retrieved_date, facility_key, match_method, match_confidence, last_seen_release)",
            "ON CONFLICT (row_hash) DO UPDATE SET last_seen_release = EXCLUDED.last_seen_release",
            [b[0] for b in batch])
        db.query(sql, params)
        sql, params = _multi(
            "INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key,"
            " field_key, date_key, value, basis, site_visit, row_hash, confidence, source_class,"
            " asserted_at)",
            "ON CONFLICT (assertion_id, release_tag) DO NOTHING",
            [b[1] for b in batch])
        db.query(sql, params)
        inserted += getattr(db, "last_row_count", 0) or 0
    return {"offered": len(assertions), "inserted": inserted,
            "already_present": len(assertions) - inserted}


# ---------------------------------------------------------------- stage 13: rebuilding golden
# Enrichment appends assertions, and until something applies survivorship to them they are invisible
# to everything that reads the database: golden_facility is what the site, the exports and
# SELECT_GOLDEN above all read. Layers 1-8 rebuild golden from the assertions of the release they
# just computed, in memory, so they neither see enrichment's rows nor preserve them — the next
# release drops them. That is accepted while the stages are separate actions (docs/enrichment.md);
# this stage is what makes the run visible until then.
SELECT_ASSERTIONS = """
    SELECT facility_key AS facility_id, source_key AS source_id,
           COALESCE(source_class, '')  AS source_class,
           COALESCE(date_key, '')      AS retrieved_date,
           COALESCE(row_hash, '')      AS row_hash,
           COALESCE(basis, 'none')     AS basis,
           COALESCE(site_visit, 0)     AS site_visit,
           COALESCE(confidence, 0)     AS confidence,
           COALESCE(asserted_at, '')   AS asserted_at,
           field_key AS field, value
    FROM fact_assertions a
    WHERE a.release_tag = $1
       OR (a.source_class = 'enrichment'
           AND EXISTS (SELECT 1 FROM fact_assertions c
                        WHERE c.release_tag = $2 AND c.facility_key = a.facility_key))
    ORDER BY facility_key, field_key, assertion_id
    LIMIT $3 OFFSET $4
"""
# $1 and $2 are the same release tag, deliberately numbered apart. Neon's HTTP endpoint binds
# numbered parameters, so reusing $1 worked there; the psycopg path translates placeholders
# positionally, where one $1 and one reference to it are two placeholders and one value. Giving
# each its own number is the form both drivers read the same way.

GOLDEN_COLUMN_SQL = 'ALTER TABLE golden_facility ADD COLUMN IF NOT EXISTS "{}" TEXT'

UPSERT_SOURCE = """
INSERT INTO dim_source (source_key, source_id, name, class, method, status_basis, status)
VALUES ($1,$2,$3,$4,NULL,NULL,'active')
ON CONFLICT (source_key) DO UPDATE SET name = EXCLUDED.name, class = EXCLUDED.class
"""


def fetch_assertions(db, release_tag: str, page: int = 5000) -> list[dict]:
    """One release's assertions, plus enrichment for the facilities that release still contains.

    Scoping to the release tag alone was nearly right and quietly destructive. It is correct that
    rebuilding from every tag would resurrect facilities a later release dropped, so golden would
    stop being a statement about the current release. But enrichment writes under the tag that was
    current when it ran, so the moment layers 1-8 published a new release, every enrichment
    assertion fell outside the scope and re-running promote could not bring it back. On the release
    database that stranded 3,223 assertions across 2,006 facilities that were still present —
    including 1,408 Geocodio rooftop lookups that had been paid for.

    The EXISTS clause is what separates the two cases: an enrichment assertion is carried forward
    only for a facility the current release still asserts something about, so a dropped facility
    stays dropped and paid work is not thrown away with it.
    """
    out, offset = [], 0
    while True:
        got = db.query(SELECT_ASSERTIONS, (release_tag, release_tag, page, offset))
        for r in got:
            # '?' is the sentinel golden.py uses for an unknown class. It cannot be written as a
            # SQL literal here: shared-dialect SQL uses '?' as its parameter placeholder and the
            # postgres adapter rewrites every one it finds, including the ones inside quotes.
            r["source_class"] = r["source_class"] or "?"
        out.extend(got)
        if len(got) < page:
            return out
        offset += page


def golden_columns(db) -> set[str]:
    return {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'golden_facility'", ())}


def missing_assertion_columns(db) -> list[str]:
    """asserted_at, on a database whose loader predates it. Without the column the INSERT below
    fails outright, and without the value survivorship cannot order two same-day measurements."""
    have = {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'fact_assertions'", ())}
    return [c for c in ("asserted_at",) if c not in have]


def add_assertion_columns(db, cols: list[str]) -> list[str]:
    for col in cols:
        db.query(f'ALTER TABLE fact_assertions ADD COLUMN IF NOT EXISTS "{col}" TEXT', ())
    return list(cols)


def missing_golden_columns(db, fields: list[str]) -> list[str]:
    """Columns this build knows that the database has not got.

    Enrichment can run against a database whose last loader predates a new golden field, and an
    INSERT naming a column that is not there fails the whole rebuild.
    """
    have = golden_columns(db)
    return [x for f in fields for x in (f, f"{f}__source") if x not in have]


def add_golden_columns(db, cols: list[str]) -> list[str]:
    for col in cols:
        db.query(GOLDEN_COLUMN_SQL.format(col), ())
    return list(cols)


def register_sources(db, sources: dict) -> int:
    """dim_source rows for the enrichment source ids, so v_provenance can name who says so."""
    n = 0
    for sid, meta in sources.items():
        db.query(UPSERT_SOURCE, (sid, sid, meta["name"], meta["class"]))
        n += 1
    return n


def replace_golden(db, rows: list[dict], fields: list[str], release_tag: str, chunk: int = 300) -> int:
    """Replace golden_facility with these rows, in chunks of one multi-row INSERT each.

    Chunked because NeonHttp.executemany is one HTTPS round trip per row: 4,000 of those is over an
    hour and the stage times out at 35 minutes. Postgres caps a statement at 65,535 parameters, so
    the chunk size is bounded by columns-per-row; 300 x 30 leaves plenty of room.
    """
    # Migration-backed promotion is atomic and preserves feedback received after
    # this stage read its snapshot. Fallback supports warehouses before migration.
    available = db.query("SELECT to_regprocedure('replace_golden_with_feedback(jsonb,text)') IS NOT NULL AS ready", ())
    if available and available[0].get("ready"):
        import json
        payload = [{"facility_key": g["facility_id"], "release_tag": release_tag,
                    **{x: _text(g.get(x)) for f in fields for x in (f, f"{f}__source")},
                    "n_assertions": g.get("n_assertions"), "n_sources": g.get("n_sources")} for g in rows]
        result = db.query("SELECT replace_golden_with_feedback($1::jsonb,$2) AS written", (json.dumps(payload), release_tag))
        return int(result[0]["written"])
    cols = ["facility_key", "release_tag"] + [x for f in fields for x in (f, f"{f}__source")] + \
           ["n_assertions", "n_sources"]
    quoted = ", ".join(f'"{c}"' for c in cols)
    db.query("DELETE FROM golden_facility", ())
    written = 0
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        params, values, n = [], [], 0
        for g in batch:
            vals = [g["facility_id"], release_tag] + \
                   [_text(g.get(x)) for f in fields for x in (f, f"{f}__source")] + \
                   [g.get("n_assertions"), g.get("n_sources")]
            values.append("(" + ",".join(f"${n + j + 1}" for j in range(len(vals))) + ")")
            params.extend(vals)
            n += len(vals)
        db.query(f"INSERT INTO golden_facility ({quoted}) VALUES {','.join(values)}", params)
        written += len(batch)
    return written


def _text(v):
    return None if v in (None, "") else str(v)


def golden_coverage(db, fields: list[str]) -> dict[str, int]:
    """How many golden rows carry each field, counted in the database rather than in a snapshot.

    SELECT_GOLDEN reads the eight columns the stages need, so measuring the "before" side of gate
    E6 from it would report zero for every field it does not select and the gate would wave through
    a rebuild that dropped them. COUNT(col) ignores nulls, which is exactly the question.
    """
    cols = ", ".join(f'COUNT("{f}") AS "{f}"' for f in fields)
    row = db.query(f"SELECT COUNT(*) AS __rows, {cols} FROM golden_facility", ())[0]
    return {k: int(v or 0) for k, v in row.items()}
