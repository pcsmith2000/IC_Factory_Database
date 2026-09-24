"""A person ruling a facility not IC keeps it out of golden, and nothing else can."""
from pathlib import Path

from pipeline.enrich import gates
from pipeline.golden import build_golden, load_operator_assertions, split_excluded
from pipeline.registry import load_yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = load_yaml(ROOT / "registry" / "survivorship.yaml")


def a(fid, field, value, source, cls, basis="none", date="2026-09-01"):
    return {"facility_id": fid, "field": field, "value": value, "source_id": source, "source_class": cls,
            "basis": basis, "retrieved_date": date, "site_visit": False, "row_hash": "", "confidence": 1}


ROSTER = [a("IC-1", "name", "Nordic Sauna LLC", "osha_inspections", "B"),
          a("IC-1", "state", "CA", "osha_inspections", "B"),
          a("IC-2", "name", "Cavcon, Inc.", "epa_frs", "B")]


def test_an_operator_not_ic_ruling_takes_the_facility_out_of_golden():
    asserts = ROSTER + [a("IC-1", "existence_flag", "not_ic", "operator", "operator", "operator")]
    rows, _ = build_golden(asserts, RULES)
    assert [r["facility_id"] for r in rows] == ["IC-2"]
    kept, _, dropped = split_excluded(*build_golden(asserts, RULES, keep_excluded=True))
    assert [r["facility_id"] for r in kept] == ["IC-2"] and [r["facility_id"] for r in dropped] == ["IC-1"]


def test_an_employee_ruling_counts_and_its_conflicts_leave_with_it():
    asserts = ROSTER + [a("IC-1", "state", "NV", "epa_frs", "B"),
                        a("IC-1", "existence_flag", "not_ic", "adl_employee_feedback", "human_feedback", "human_verified")]
    rows, conflicts = build_golden(asserts, RULES)
    assert {r["facility_id"] for r in rows} == {"IC-2"}
    assert not [c for c in conflicts if c["facility_id"] == "IC-1"]


def test_only_a_person_can_rule_a_facility_out():
    # Stage 12 is advisory (gate E5); a machine source saying not_ic must not delete a plant.
    rows, _ = build_golden(ROSTER + [a("IC-1", "existence_flag", "not_ic", "enrich:existence", "enrichment", "advisory")], RULES)
    assert {r["facility_id"] for r in rows} == {"IC-1", "IC-2"}


def test_a_later_review_ruling_puts_the_facility_back():
    asserts = ROSTER + [a("IC-1", "existence_flag", "not_ic", "operator", "operator", "operator", "2026-09-24"),
                        a("IC-1", "existence_flag", "review", "operator", "operator", "operator", "2026-10-01")]
    rows, _ = build_golden(asserts, RULES)
    assert {r["facility_id"] for r in rows} == {"IC-1", "IC-2"}


def test_e6_tolerates_exactly_the_ruled_out_rows():
    before = {"__rows": 10, "name": 10}
    after = {"__rows": 8, "name": 8}
    assert gates.e6_rebuilt_golden_loses_nothing(before, after, {"__rows": 2, "name": 2}).passed
    assert not gates.e6_rebuilt_golden_loses_nothing(before, after, {"__rows": 1, "name": 1}).passed


def test_the_committed_rulings_load_as_operator_assertions():
    ops = load_operator_assertions(ROOT / "control" / "operator_assertions.csv")
    ruled = {o["facility_id"] for o in ops if o["field"] == "existence_flag" and o["value"] == "not_ic"}
    assert {"IC-95860", "IC-70761", "IC-94220", "IC-10222"} <= ruled
