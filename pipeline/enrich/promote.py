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
    return golden_mod.build_golden(assertions, rules, keep_excluded=True)


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[str(i.get(key))] = out.get(str(i.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def parse_allowed_loss(text: str) -> dict[str, int]:
    """An operator's one-run allowance for gate E6, spelled `field=count,field=count`.

    E6 tolerates on its own exactly what E10 and E11 withheld. It cannot tolerate a loss that a
    RULE change moves rather than withholds: promote #87 asked the unique-name rule before the
    address rule, six existence verdicts left the duplicate rows they had been carried to, and
    the plants they belong to already held one, so the count fell by six and the gate halted a
    rebuild that was right. The allowance is declared per run, by field, never stored, and is
    written into the promote report so the loss it excused is on the record.
    """
    out: dict[str, int] = {}
    for part in (text or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"allowed loss must be field=count, got {part!r}")
        field, _, count = part.partition("=")
        field, count = field.strip(), count.strip()
        if field not in GOLDEN_FIELDS:
            raise ValueError(f"allowed loss names {field!r}, which is not a golden field")
        if not count.isdigit():
            raise ValueError(f"allowed loss for {field} must be a whole number, got {count!r}")
        out[field] = max(out.get(field, 0), int(count))
    return out


class _Numbered:
    """The warehouse-dialect reader golden_refresh.compute expects ('?' placeholders), over the
    enrichment database, which binds Postgres' numbered ones ($1, $2, ...)."""
    engine = "postgres"

    def __init__(self, db):
        self.db = db

    def query(self, sql: str, params=()):
        import re
        n = iter(range(1, len(params) + 1))
        return self.db.query(re.sub(r"\?", lambda _: f"${next(n)}", sql), tuple(params))


def run(db, release_tag: str, dry_run: bool = False, allowed_loss: dict[str, int] | None = None,
        batch: int = 400) -> dict:
    from .. import golden_refresh
    rules = load_yaml(ROOT / "registry" / "survivorship.yaml")
    operator_loss = dict(allowed_loss or {})
    # Widening comes first: the before-coverage below counts every golden column, and a column this
    # build knows that the database has not got yet would make that count fail rather than read 0.
    # Adding a nullable column changes no existing row, so it is safe ahead of the gate.
    missing = _db.missing_golden_columns(db, GOLDEN_FIELDS)
    added = [] if dry_run else _db.add_golden_columns(db, missing)
    # A dry run writes nothing, so it measures only the columns the database actually has.
    measurable = [f for f in GOLDEN_FIELDS if f not in missing] if dry_run else GOLDEN_FIELDS
    cov_before = _db.golden_coverage(db, measurable)

    # The golden basis comes through the permanent registry (#39, #49): each fact reaches the plant
    # its own release meant (v_assertions_resolved), so the name-and-address carry E11 used to make
    # is gone, and with it the carried assertions it had to withhold. golden_refresh.compute_full is
    # the one implementation both this stage and the continuous refresh use.
    reader = _Numbered(db)
    ids = golden_refresh.active_facilities(reader)
    if not ids:
        return {"release_tag": release_tag, "halted": True, "written": 0, "columns_added": added,
                "columns_missing": missing, "allowed_loss_operator": operator_loss, "gates": [],
                "reason": "the permanent facility registry is empty: run `python -m pipeline.facility_registry seed`"}
    rows, unfiltered_rows, quarantined, excluded, conflicts = [], [], [], [], []
    for i in range(0, len(ids), batch):
        part = golden_refresh.compute_full(reader, ids[i:i + batch], release_tag, rules)
        rows.extend(part["rows"].values())
        unfiltered_rows.extend(part["unfiltered"])
        quarantined.extend(part["quarantined"])
        excluded.extend(sorted(part["excluded"]))
        conflicts.extend(part["conflicts"])
    cov_after = coverage(rows, measurable)
    from . import gates
    # E6 must tolerate exactly what E10 withheld and the facilities a person ruled not IC, and
    # nothing else: the difference between the rebuild before and after those, field by field.
    cov_unfiltered = coverage(unfiltered_rows, measurable)
    # An operator may add a declared allowance on top (parse_allowed_loss); the larger of the two
    # applies per field, and both are reported so a reader can see what was excused and by whom.
    allowed_loss = {f: max(0, cov_unfiltered[f] - cov_after[f], operator_loss.get(f, 0)) for f in [*measurable, "__rows"]}
    results = gates.run_promote(cov_before, cov_after, allowed_loss=allowed_loss)
    results.append(gates.e10_no_coordinate_outside_its_state(quarantined))

    out = {"release_tag": release_tag, "facilities_registered": len(ids),
           "golden_rows": len(rows), "conflicts": len(conflicts),
           "survivorship_version": rules.get("version"),
           "coverage_before": cov_before, "coverage_after": cov_after,
           "gained": {f: cov_after[f] - cov_before.get(f, 0) for f in measurable
                      if cov_after[f] != cov_before.get(f, 0)},
           "gates": [str(r) for r in results], "written": 0,
           "allowed_loss": {f: n for f, n in allowed_loss.items() if n},
           "allowed_loss_operator": operator_loss,
           "excluded_not_ic": excluded,
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
