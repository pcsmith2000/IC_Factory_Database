"""Enrichment gates. Like G1-G5 in layers 1-8, these block rather than advise.

Enrichment adds fields to a release that has already passed its own gates, so the risk it carries
is not that it finds too little — that is visible and harmless — but that it quietly makes the
release worse: a value that cannot be traced, a coordinate that should never have been published,
or a run that takes a field away from a facility that had one. Each gate is a specific way that
could happen, expressed so that a run stops rather than publishes.
"""
from __future__ import annotations
from dataclasses import dataclass


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


# A coordinate may be published on either of these bases and no other. Two entries, not a blanket
# allowance: the point of E2 is that a coordinate names how it was arrived at, and every new way
# of arriving at one is a deliberate addition here with a measurement behind it.
COORDINATE_BASES = {
    "rooftop",       # Geocodio, accuracy_type=rooftop
    "place_match",   # stage 13, an Overture place at the same street address (see places.py)
    # stage 14: a coordinate Geocodio computed and its own accuracy_type refused, kept only when
    # the postcode it returned is ours AND an Overture building stands within the radius. The
    # label is Geocodio's opinion of its method; the building is a measurement (see anchor.py).
    "interpolated_on_building",
}


def e2_no_coordinate_from_a_non_rooftop_geocode(assertions: list[dict]) -> GateResult:
    """Measured on 30 geocodes checked against imagery: rooftop verified 80% of the time and its
    failures were the right site off by 25-110m; nearest_rooftop_match verified 30% and its
    failures were a different parcel entirely. A point on the wrong parcel is worse than none,
    because stage 11 measures the building under it and the map renders it as known.

    `place_match` joined it on its own measurement, not by being waved through: against 241
    facilities with a verified rooftop coordinate it had a median error of 63m and a p90 of 302m
    (pipeline/enrich/places.py). That is worse than rooftop, which is why survivorship ranks it
    below rooftop and why it is a separate basis rather than being called one.
    """
    coords = [a for a in assertions if a.get("field") == "lat_lon"]
    bad = [a for a in coords if a.get("basis") not in COORDINATE_BASES]
    return GateResult("E2", not bad,
                      f"{len(coords)} coordinates, {len(bad)} on a basis that is not "
                      f"{' or '.join(sorted(COORDINATE_BASES))}")


def e8_every_anchored_coordinate_names_its_building(assertions: list[dict]) -> GateResult:
    """An interpolated point is believable here only because a building was found under it. Without
    the Overture id and the distance in the evidence, the claim cannot be re-checked when Overture
    changes and is indistinguishable from publishing the accuracy_type we set out to stop trusting.
    """
    an = [a for a in assertions if a.get("basis") == "interpolated_on_building"]
    bad = [a for a in an if "building:" not in (a.get("evidence") or "")
           or "confirmed by" not in (a.get("evidence") or "")]
    return GateResult("E8", not bad,
                      f"{len(an)} anchored coordinates, {len(bad)} without an Overture building id")


def e7_every_place_match_cites_the_address_that_agreed(assertions: list[dict]) -> GateResult:
    """A place match is believable only because two independently-sourced addresses agreed. The
    evidence has to carry the Overture place id AND both addresses, or the agreement cannot be
    re-checked and the value is indistinguishable from a coordinate someone typed."""
    pm = [a for a in assertions if a.get("basis") == "place_match"]
    bad = [a for a in pm
           if "overture:" not in (a.get("evidence") or "") or ":: matched " not in (a.get("evidence") or "")]
    return GateResult("E7", not bad,
                      f"{len(pm)} place matches, {len(bad)} without the Overture id and both addresses")


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


def e6_rebuilt_golden_loses_nothing(before: dict[str, int], after: dict[str, int]) -> GateResult:
    """Stage 13 replaces golden_facility outright, so it is the one stage that can destroy the
    release rather than merely fail to improve it. The rebuild is survivorship applied to the
    assertions of the same release the loader used, plus enrichment's, so every field must come
    back at least as covered as it went in. A field that shrank means the assertions were read
    wrong or the rules changed under us, and either way the old golden is the better one.

    E4 asks the same question of the stage outputs; this asks it of what actually lands in the
    table, which is the only version a reader ever sees.
    """
    lost = {f: (before[f], after.get(f, 0)) for f in before
            if f != "__rows" and after.get(f, 0) < before[f]}
    n0, n1 = before.get("__rows", 0), after.get("__rows", 0)
    ok = not lost and n1 >= n0
    detail = ", ".join(f"{f} {a}->{b}" for f, (a, b) in sorted(lost.items()))
    return GateResult("E6", ok,
                      f"{n0} golden rows -> {n1}"
                      + (f" — lost coverage: {detail}" if lost else "")
                      + ("" if n1 >= n0 else " — fewer facilities than before"))


def e9_every_capability_is_a_member_of_the_taxonomy(assertions: list[dict]) -> GateResult:
    """Stage 15 answers in words a model chose, so the one thing that must never reach golden is
    a capability that is not in registry/taxonomy.yaml. The parser already drops an answer it
    cannot resolve exactly; this is the gate that says so out loud, because a stage that both
    validates and coerces has no validation — and because the taxonomy is a FILE, so a leaf
    renamed there must fail the run rather than silently orphan every value carrying the old name.
    """
    from . import capability
    tx = capability.load()
    rows = [a for a in assertions
            if a.get("field") in ("capability_group", "capability_leaf")]
    bad = [a for a in rows
           if (a["value"] not in tx.leaves if a["field"] == "capability_leaf"
               else a["value"] not in tx.groups)]
    names = ", ".join(sorted({a["value"] for a in bad})[:5])
    return GateResult("E9", not bad,
                      f"{len(rows)} capability values, {len(bad)} outside taxonomy v{tx.version}"
                      + (f" ({names})" if bad else ""))


def run_promote(before: dict[str, int], after: dict[str, int]) -> list[GateResult]:
    return [e6_rebuilt_golden_loses_nothing(before, after)]


def run_all(assertions: list[dict], before: list[dict] | None = None,
            after: list[dict] | None = None) -> list[GateResult]:
    results = [e1_every_located_address_is_cited(assertions),
               e2_no_coordinate_from_a_non_rooftop_geocode(assertions),
               e3_every_footprint_names_its_building(assertions),
               e5_existence_is_advisory(assertions),
               e7_every_place_match_cites_the_address_that_agreed(assertions),
               e8_every_anchored_coordinate_names_its_building(assertions),
               e9_every_capability_is_a_member_of_the_taxonomy(assertions)]
    if before is not None and after is not None:
        results.insert(3, e4_enrichment_never_removes_a_field(before, after))
    return results
