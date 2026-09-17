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
    """Postgres when the driver and port are available, else the same database over HTTPS."""
    import os
    url = url or os.environ.get("DATABASE_URL_UNPOOLED") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("no DATABASE_URL / DATABASE_URL_UNPOOLED for the enrichment branch")
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8):
            pass
        from ..warehouse import open_warehouse
        return open_warehouse()
    except Exception:
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


def append(db, assertions: list[dict], release_tag: str) -> dict:
    """Append enrichment assertions. ON CONFLICT DO NOTHING plus the evidence-derived row_hash is
    what makes a re-run a no-op rather than a duplicate.

    Reports rows actually inserted, not rows offered. A second run over unchanged evidence offers
    the same assertions and inserts none of them, and a summary that called that "30 appended"
    would be reporting the opposite of the property the design depends on.
    """
    from datetime import date
    inserted = skipped = 0
    for a in assertions:
        # source_url means a URL. A located address cites a page, so it has one; a footprint cites
        # an Overture release and building id and a geocode cites a parcel dataset, and neither is
        # a URL. Putting those in source_url would make the column mean "whatever the evidence was"
        # and anything reading it as a link would be wrong.
        ev = (a.get("evidence") or "")
        if ev.startswith("http"):
            url, _, quote = ev.partition(" :: ")
        else:
            url, quote = "", ev
        db.query(APPEND_EVIDENCE, (a["row_hash"], a["source_id"], url, quote,
                                   a.get("retrieved_date") or date.today().isoformat(),
                                   a["facility_id"], a.get("basis", "none"),
                                   None if a.get("confidence") in ("", None) else float(a["confidence"]),
                                   release_tag))
        aid = f"{a['source_id']}|{a['facility_id']}|{a['field']}|{a['row_hash']}"
        db.query(APPEND_ASSERTION, (aid, release_tag, a["facility_id"], a["source_id"], a["field"],
                                    a.get("retrieved_date") or date.today().isoformat(),
                                    a["value"], a.get("basis", "none"), 0, a["row_hash"],
                                    None if a.get("confidence") in ("", None) else float(a["confidence"]),
                                    a.get("source_class", "enrichment")))
        if getattr(db, "last_row_count", 1):
            inserted += 1
        else:
            skipped += 1
    return {"offered": len(assertions), "inserted": inserted, "already_present": skipped}
