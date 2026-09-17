"""Enrichment gates. Like G1-G5 in layers 1-8, these block rather than advise.

Enrichment adds fields to a release that has already passed its own gates, so the risk it carries
is not that it finds too little — that is visible and harmless — but that it quietly makes the
release worse: a value that cannot be traced, a coordinate that should never have been published,
or a run that takes a field away from a facility that had one. Each gate is a specific way that
could happen, expressed so that a run stops rather than publishes.
"""
from __future__ import annotations
from dataclasses import dataclass

SMALL = 10_000


@dataclass
class GateResult:
    gate: str
    passed: bool
    summary: str

    def __str__(self):
        return f"{'PASS' if self.passed else 'FAIL'} {self.gate}: {self.summary}"


def e1_every_located_address_is_cited(assertions: list[dict]) -> GateResult:
    """A located address must carry the page it was read from and the sentence it appeared in.
    Once a value reaches golden_facility nothing downstream can tell a recalled address from a
    read one, so the citation is the only thing standing between search and confabulation."""
    located = [a for a in assertions if a.get("source_id") == "enrich:locate"]
    bad = [a for a in located if "::" not in (a.get("evidence") or "")
           or not (a.get("evidence") or "").startswith("http")]
    return GateResult("E1", not bad,
                      f"{len(located)} located, {len(bad)} without a URL and a quote")


def e2_no_coordinate_from_a_non_rooftop_geocode(assertions: list[dict]) -> GateResult:
    """Measured on 30 geocodes checked against imagery: rooftop verified 80% of the time and its
    failures were the right site off by 25-110m; nearest_rooftop_match verified 30% and its
    failures were a different parcel entirely. A point on the wrong parcel is worse than none,
    because stage 11 measures the building under it and the map renders it as known."""
    coords = [a for a in assertions if a.get("field") == "lat_lon"]
    bad = [a for a in coords if a.get("basis") != "rooftop"]
    return GateResult("E2", not bad,
                      f"{len(coords)} coordinates, {len(bad)} not from a rooftop geocode")


def e3_every_footprint_names_its_building(assertions: list[dict]) -> GateResult:
    """A square footage with no building id behind it cannot be re-checked when Overture changes,
    and cannot be distinguished from a number someone typed."""
    fps = [a for a in assertions if a.get("field") == "building_sqft"]
    bad = [a for a in fps if ":" not in (a.get("evidence") or "")]
    return GateResult("E3", not bad,
                      f"{len(fps)} footprints, {len(bad)} without an Overture building id")


def e4_enrichment_never_removes_a_field(before: list[dict], after: list[dict]) -> GateResult:
    """The important one. Enrichment only ever adds. If a run would leave fewer facilities with an
    address or a coordinate than it found, something upstream broke and publishing would bake the
    regression into the release."""
    def counts(rows):
        return (sum(1 for r in rows if (r.get("address") or "").strip()),
                sum(1 for r in rows if (r.get("lat_lon") or "").strip()))
    a0, c0 = counts(before)
    a1, c1 = counts(after)
    ok = a1 >= a0 and c1 >= c0
    return GateResult("E4", ok,
                      f"addresses {a0}->{a1}, coordinates {c0}->{c1}"
                      + ("" if ok else " — a field was lost"))


def e5_existence_is_advisory(assertions: list[dict]) -> GateResult:
    """Stage 12 may flag a facility for review and may not retire one. Retiring is a decision, and
    decisions live in control/operator_assertions.csv, which outranks every source including this."""
    ex = [a for a in assertions if a.get("source_id") == "enrich:existence"]
    bad = [a for a in ex if a.get("field") != "existence_flag" or a.get("value") != "review"]
    return GateResult("E5", not bad,
                      f"{len(ex)} existence flags, {len(bad)} that were not advisory")


def run_all(assertions: list[dict], before: list[dict] | None = None,
            after: list[dict] | None = None) -> list[GateResult]:
    results = [e1_every_located_address_is_cited(assertions),
               e2_no_coordinate_from_a_non_rooftop_geocode(assertions),
               e3_every_footprint_names_its_building(assertions),
               e5_existence_is_advisory(assertions)]
    if before is not None and after is not None:
        results.insert(3, e4_enrichment_never_removes_a_field(before, after))
    return results
