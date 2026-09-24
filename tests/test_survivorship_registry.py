"""registry/survivorship.yaml is shaped the way golden and the warehouse read it.

A misplaced top-level key once swallowed existence_flag and employee_notes into `derived:` (#33):
golden silently fell back to default_order, dropping existence_flag's `basis:advisory` rule, and
nothing failed. These tests make that shape a CI failure.
"""
import json
from pathlib import Path

from pipeline.enrich.identity import ADDRESS_BOUND_FIELDS
from pipeline.golden import build_golden
from pipeline.registry import load_yaml
from pipeline.warehouse import GOLDEN_FIELDS, dim_field_rows

RULES = load_yaml(Path(__file__).resolve().parents[1] / "registry" / "survivorship.yaml")


def test_every_non_derived_golden_field_has_its_own_rule():
    derived = {f for f, rule in (RULES.get("derived") or {}).items() if rule.get("from")}
    missing = [f for f in GOLDEN_FIELDS if f not in derived and f not in RULES["fields"]]
    assert not missing, f"golden fields with no entry under `fields:` (misplaced key?): {missing}"


def test_every_derived_rule_names_what_it_is_derived_from():
    for field, rule in (RULES.get("derived") or {}).items():
        assert rule.get("from"), f"derived.{field} has no `from:` — it belongs under `fields:`"
        assert field in GOLDEN_FIELDS


def test_existence_flag_keeps_its_advisory_rule():
    assert "basis:advisory" in RULES["fields"]["existence_flag"]["order"]
    a = dict(facility_id="IC-1", field="existence_flag", source_class="enrichment", retrieved_date="2026-09-01",
             site_visit=False, row_hash="", confidence=1)
    rows, _ = build_golden([dict(a, value="advisory_value", source_id="overture:building", basis="advisory"),
                            dict(a, value="class_a_value", source_id="some_registry", source_class="A", basis="none")], RULES)
    assert rows[0]["existence_flag"] == "advisory_value"


def test_flag_fields_are_not_golden_fields():
    assert "geocode_quality" in RULES["flag_fields"]
    assert not set(RULES["flag_fields"]) & set(GOLDEN_FIELDS)


def test_every_address_bound_field_has_a_dim_field_row():
    keys = {r[0] for r in dim_field_rows(RULES)}
    assert set(ADDRESS_BOUND_FIELDS) <= keys


def test_dim_field_carries_the_order_survivorship_ranks_by():
    rows = {r[0]: r for r in dim_field_rows(RULES)}
    assert json.loads(rows["existence_flag"][2]) == RULES["fields"]["existence_flag"]["order"]
    assert json.loads(rows["floor_area_sqft"][2]) == RULES["derived"]["floor_area_sqft"]["order"]
    assert rows["geocode_quality"][2] is None
    assert len(rows) == len(GOLDEN_FIELDS) + len(RULES["flag_fields"])
