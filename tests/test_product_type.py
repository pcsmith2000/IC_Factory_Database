"""The classifier's IC product category, from its JSON to the golden column.

This path was silently broken: product_type was declared in warehouse.GOLDEN_FIELDS, given a rule
in survivorship.yaml and documented in docs/data-model.md, but nothing ever asserted it, so the
column was NULL for all 4,065 facilities of release v1.0.0+reg.22389a4. These tests pin each hop.
"""
from pathlib import Path
from pipeline import classify, golden
from pipeline.registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent
RULES = load_yaml(ROOT / "registry" / "survivorship.yaml")


def test_product_type_normalises_the_vocabulary():
    assert classify.product_type({"type": "panel"}) == "panel"
    assert classify.product_type({"type": " Mass-Timber "}) == "mass_timber"      # case, dashes, space
    assert classify.product_type({"type": "HUD_CODE"}) == "hud_code"


def test_product_type_outside_the_vocabulary_becomes_other():
    # run 35170703333 answered "wood" once in 2228 IC rows
    assert classify.product_type({"type": "wood"}) == "other"
    assert classify.product_type({"type": "sips"}) == "other"


def test_none_and_missing_assert_nothing():
    # "none" is the contract's answer for a NOT-IC row; it carries no information
    assert classify.product_type({"type": "none"}) is None
    assert classify.product_type({"type": ""}) is None
    assert classify.product_type({}) is None
    assert classify.product_type({"type": 7}) is None


def _row(**kw):
    base = {"facility_id": "IC-00001", "source_id": "epa_frs", "retrieved_date": "2026-09-17",
            "row_hash": "h1", "name_verbatim": "ACME MODULAR", "status_basis": "none"}
    base.update(kw)
    return base


def test_classifier_product_type_becomes_its_own_assertion():
    a = golden.assertions_from_rows([_row(product_type="volumetric")], {"epa_frs": "A"})
    pt = [x for x in a if x["field"] == "product_type"]
    assert len(pt) == 1
    assert pt[0]["value"] == "volumetric"
    # provenance: the claim is the model's, not the roster's
    assert pt[0]["source_id"] == "classifier"
    assert pt[0]["source_class"] == "classifier"
    # the name assertion still belongs to the source
    assert [x for x in a if x["field"] == "name"][0]["source_id"] == "epa_frs"


def test_no_product_type_asserts_nothing():
    a = golden.assertions_from_rows([_row()], {"epa_frs": "A"})
    assert not [x for x in a if x["field"] == "product_type"]


def test_survivorship_lets_an_operator_override_the_model():
    """The rule order is [operator, classifier]; a human correction has to win."""
    model = golden.assertions_from_rows([_row(product_type="panel")], {"epa_frs": "A"})
    human = {"facility_id": "IC-00001", "field": "product_type", "value": "volumetric",
             "source_id": "operator", "source_class": "operator", "retrieved_date": "2026-09-18",
             "basis": "none", "row_hash": "", "confidence": ""}
    g, _ = golden.build_golden(model + [human], RULES)
    assert g[0]["product_type"] == "volumetric"
    assert g[0]["product_type__source"] == "operator"


def test_classifier_wins_when_no_operator_says_otherwise():
    g, _ = golden.build_golden(golden.assertions_from_rows([_row(product_type="precast")], {"epa_frs": "A"}), RULES)
    assert g[0]["product_type"] == "precast"
    assert g[0]["product_type__source"] == "classifier"


def test_bare_source_rank_still_resolves_operator_and_lookup():
    """_rank was generalised from three hardcoded ids to a bare-token match; the old two must hold."""
    order = ["operator", "lookup"]
    assert golden._rank({"source_id": "operator", "source_class": "operator", "basis": "none"}, order) == 0
    assert golden._rank({"source_id": "lookup", "source_class": "lookup", "basis": "none"}, order) == 1
    assert golden._rank({"source_id": "epa_frs", "source_class": "A", "basis": "none"}, order) == 2


# ------------------------------------------------- the audit, per product category
def test_audit_locates_false_positives_by_product_type():
    """The floor as one number says "something is wrong"; per category it says where.
    On run 35174109197 panel carried 23.6% and hud_code 0.2% — same prompt, same model."""
    from pipeline import audit
    names = ["MASONITE MOBILE", "FLORIDA PLYWOODS, INC.", "NORTH BAY PLYWOOD",
             "CLAYTON HOMES, INC.", "CUSTOM ROOF TRUSSES"]
    types = ["panel", "panel", "panel", "hud_code", "truss_component"]
    r = audit.scan(names, types)
    assert r["by_product_type"]["panel"]["flagged"] == 3
    assert r["by_product_type"]["panel"]["rate"] == 1.0
    assert r["by_product_type"]["hud_code"]["rate"] == 0.0
    assert r["by_product_type"]["truss_component"]["rate"] == 0.0
    assert "concentrated in: panel" in audit.line(r)


def test_audit_without_product_types_still_works():
    """Callers that only have names keep the old behaviour."""
    from pipeline import audit
    r = audit.scan(["MASONITE MOBILE", "ACME MODULAR"])
    assert r["flagged"] == 1 and r["by_product_type"] == {}
    assert "concentrated in" not in audit.line(r)
