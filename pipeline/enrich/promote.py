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
from . import _db

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
    rows, conflicts = build(asserts, rules)
    cov_after = coverage(rows, measurable)
    from . import gates
    results = gates.run_promote(cov_before, cov_after)

    out = {"release_tag": release_tag, "assertions_read": len(asserts),
           "golden_rows": len(rows), "conflicts": len(conflicts),
           "survivorship_version": rules.get("version"),
           "coverage_before": cov_before, "coverage_after": cov_after,
           "gained": {f: cov_after[f] - cov_before.get(f, 0) for f in measurable
                      if cov_after[f] != cov_before.get(f, 0)},
           "gates": [str(r) for r in results], "written": 0,
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
