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

SELECT_GOLDEN = """
    SELECT COALESCE(d.facility_id, g.facility_key) AS facility_id, g.facility_key,
           g.name, g.address, g.city, g.state, g.zip, g.lat_lon, g.status, g.expiry_date,
           d.tier, g.release_tag
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
