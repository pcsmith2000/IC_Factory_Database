"""Stage 13 — apply survivorship to everything the release now asserts, and replace golden.

Stages 9-12 append assertions and stop. Nothing downstream reads fact_assertions: golden_facility
is what the site, the exports and enrichment's own SELECT_GOLDEN all read, so until survivorship
runs again an enriched release is indistinguishable from an un-enriched one. This is the stage
that closes that gap.

It is deliberately not a special case. It calls the same golden.build_golden with the same
registry/survivorship.yaml that layers 1-8 use, over the assertions of the same release tag. A
Geocodio rooftop wins over an EPA coordinate because survivorship.yaml ranks `basis:rooftop` above
`class:B`, not because this stage prefers it — and an operator correction still beats both.

What it does not do is survive. Layers 1-8 rebuild golden from the assertions they hold in memory,
so the next release drops enrichment's fields until the two pipelines are one. That is the accepted
cost of keeping the stages as separate actions while the design is still moving.
"""
from __future__ import annotations
from pathlib import Path

from .. import golden as golden_mod
from ..registry import load_yaml
from ..warehouse import GOLDEN_FIELDS, SYNTHETIC_SOURCES
from . import _db, geo, identity

ROOT = Path(__file__).resolve().parent.parent.parent


def coverage(rows: list[dict], fields: list[str]) -> dict[str, int]:
    """How many facilities carry each golden field. The measure gate E6 compares across a rebuild."""
    out = {"__rows": len(rows)}
    for f in fields:
        out[f] = sum(1 for r in rows if (r.get(f) or "") != "")
    return out


def build(assertions: list[dict], rules: dict) -> tuple[list[dict], list[dict]]:
    """Survivorship over the release's assertions. `retrieved_date` is the tie-break and the
    warehouse stores it as a nullable date_key, so it is coalesced to '' on the way out of the
    database — max() over a mix of str and None raises, and a stage that dies here would look
    like a database problem rather than a missing date."""
    for a in assertions:
        a["retrieved_date"] = a.get("retrieved_date") or ""
        a["site_visit"] = a.get("site_visit") in (1, True, "1", "True")
    return golden_mod.build_golden(assertions, rules)


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[str(i.get(key))] = out.get(str(i.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def run(db, release_tag: str, dry_run: bool = False) -> dict:
    rules = load_yaml(ROOT / "registry" / "survivorship.yaml")
    # Widening comes first: the before-coverage below counts every golden column, and a column this
    # build knows that the database has not got yet would make that count fail rather than read 0.
    # Adding a nullable column changes no existing row, so it is safe ahead of the gate.
    missing = _db.missing_golden_columns(db, GOLDEN_FIELDS)
    added = [] if dry_run else _db.add_golden_columns(db, missing)
    # A dry run writes nothing, so it measures only the columns the database actually has.
    measurable = [f for f in GOLDEN_FIELDS if f not in missing] if dry_run else GOLDEN_FIELDS
    cov_before = _db.golden_coverage(db, measurable)

    asserts = _db.fetch_assertions(db, release_tag)
    unfiltered_rows, _ = build([dict(a) for a in asserts], rules)
    # Gate E11: cross-release carry-over is by IDENTITY, not by id. An assertion from an earlier
    # release is read under the current facility that has the (name, city, state) its own release
    # gave its id — the plant it was about — and withheld when no current facility has it.
    # See pipeline/enrich/identity.py.
    asserts, carried_withheld, carry_counts = identity.carry_by_identity(asserts, release_tag)
    unjudged = sum(1 for w in carried_withheld if w.get('reason') == 'no_identity_in_that_release')
    rows, conflicts = build(asserts, rules)
    # Gate E10: a coordinate carried onto a facility must lie in that facility's state. Assertions
    # travel across releases by facility id, and when ids were issued by diverging registries the
    # same number named different plants; the rooftop then lands on the wrong one. Such a
    # coordinate is withheld from golden (the assertion itself is untouched) and survivorship is
    # rerun so the next-ranked coordinate, if any, can win. Repeats until no winner is out of state.
    quarantined = []
    for _ in range(5):
        bad = geo.out_of_state(rows)
        if not bad:
            break
        quarantined.extend(bad)
        withheld = {(b['facility_id'], b['lat_lon']) for b in bad}
        asserts = [a for a in asserts if not (a['field'] == 'lat_lon' and (a['facility_id'], a['value']) in withheld)]
        rows, conflicts = build(asserts, rules)
    cov_after = coverage(rows, measurable)
    from . import gates
    # E6 must tolerate exactly what E10 and E11 withheld and nothing else: the difference between
    # the rebuild with every assertion and the rebuild after the two gates, field by field.
    cov_unfiltered = coverage(unfiltered_rows, measurable)
    allowed_loss = {f: max(0, cov_unfiltered[f] - cov_after[f]) for f in measurable}
    results = gates.run_promote(cov_before, cov_after, allowed_loss=allowed_loss)
    results.append(gates.e11_carried_assertions_name_the_same_plant(carried_withheld, unjudged))
    results.append(gates.e10_no_coordinate_outside_its_state(quarantined))

    out = {"release_tag": release_tag, "assertions_read": len(asserts),
           "golden_rows": len(rows), "conflicts": len(conflicts),
           "survivorship_version": rules.get("version"),
           "coverage_before": cov_before, "coverage_after": cov_after,
           "gained": {f: cov_after[f] - cov_before.get(f, 0) for f in measurable
                      if cov_after[f] != cov_before.get(f, 0)},
           "gates": [str(r) for r in results], "written": 0,
           "carried_withheld_other_plant": len(carried_withheld),
           "carried_withheld_by_field": _count_by(carried_withheld, 'field'),
           "carried_withheld_by_source": _count_by(carried_withheld, 'source'),
           "carried_unjudged_no_name_in_release": unjudged,
           "carried_withheld_by_reason": _count_by(carried_withheld, 'reason'),
           **carry_counts,
           "carried_withheld": carried_withheld,
           "coordinates_withheld_out_of_state": len(quarantined),
           "coordinates_withheld_by_source": _count_by(quarantined, 'source'),
           "coordinates_withheld": quarantined,
           "columns_added": added, "columns_missing": missing, "sources_registered": 0}
    if any(not r.passed for r in results):
        out["halted"] = True
        return out
    if dry_run:
        out["would_write"] = len(rows)
        return out
    out["sources_registered"] = _db.register_sources(
        db, {k: v for k, v in SYNTHETIC_SOURCES.items() if v["class"] == "enrichment"})
    out["written"] = _db.replace_golden(db, rows, GOLDEN_FIELDS, release_tag)
    return out
