"""floor_area_sqft: golden's one square-footage number, chosen across sq_ft and building_sqft."""
from pipeline.golden import build_golden
from pipeline.registry import load_yaml
from pathlib import Path

RULES = load_yaml(Path(__file__).resolve().parents[1] / "registry" / "survivorship.yaml")


def a(fid, field, value, source, cls, basis="none", date="2026-09-01"):
    return {"facility_id": fid, "field": field, "value": value, "source_id": source, "source_class": cls,
            "basis": basis, "retrieved_date": date, "site_visit": False, "row_hash": "", "confidence": 1}


def by_id(rows):
    return {r["facility_id"]: r for r in rows}


def test_a_stated_size_beats_the_measured_footprint_and_the_footprint_fills_the_hole():
    rows, _ = build_golden([
        a("IC-1", "sq_ft", "120,000", "adl_july", "D"), a("IC-1", "building_sqft", "84210", "overture:building", "enrichment", "footprint"),
        a("IC-2", "building_sqft", "40500.4", "overture:building", "enrichment", "footprint"),
        a("IC-3", "sq_ft", "0", "adl_4ward", "D"), a("IC-3", "building_sqft", "9100", "overture:building", "enrichment", "footprint"),
        a("IC-4", "sq_ft", "n/a", "lead_addresses", "C"),
    ], RULES)
    g = by_id(rows)
    assert g["IC-1"]["floor_area_sqft"] == "120000" and g["IC-1"]["floor_area_sqft__source"] == "adl_july"
    assert g["IC-2"]["floor_area_sqft"] == "40500" and g["IC-2"]["floor_area_sqft__source"] == "overture:building"
    assert g["IC-3"]["floor_area_sqft"] == "9100"            # a stated 0 is not a plant size
    assert "floor_area_sqft" not in g["IC-4"]                 # nothing plausible: no column value
    assert g["IC-1"]["sq_ft"] == "120,000" and g["IC-1"]["building_sqft"] == "84210"   # the pair survives for the auditor


def test_an_operator_correction_wins_the_derived_field_too():
    rows, _ = build_golden([
        a("IC-1", "sq_ft", "120000", "adl_july", "D"), a("IC-1", "sq_ft", "95000", "operator", "operator", "operator"),
        a("IC-1", "building_sqft", "84210", "overture:building", "enrichment", "footprint"),
    ], RULES)
    assert by_id(rows)["IC-1"]["floor_area_sqft"] == "95000"


def test_floor_area_sqft_is_a_golden_column():
    from pipeline.warehouse import GOLDEN_FIELDS
    assert "floor_area_sqft" in GOLDEN_FIELDS
