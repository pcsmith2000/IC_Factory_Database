"""Stage 15 classifies what a facility MAKES, over a taxonomy that lives in a file.

Layer 3 answers a different question — does this belong here at all — and its prompt hash is part
of the release tag. Keeping the two apart is why this stage exists separately: the taxonomy can
change without invalidating the classify cache or moving every tag.
"""
from pipeline.enrich import capability as cap

TX = cap.load()


# ---- the taxonomy is a file, and resolving a written label must not score a typo

def test_adls_own_spellings_resolve_to_our_leaves():
    """ADL writes "Wood Structural Components (trusses etc)" where the taxonomy writes
    "(Trusses, etc.)". If those did not resolve to one leaf the eval would score punctuation."""
    assert TX.resolve("Wood Structural Components (trusses etc)") == "Wood Structural Components (Trusses, etc.)"
    assert TX.resolve("SIP / ICF (Other Composite panel)") == "SIP / ICF (Other Composite Panel)"
    assert TX.resolve("Precast Concrete panel") == "Precast Concrete Panel"


def test_a_bare_product_word_resolves_too():
    """A source that says `product_types: CLT` has named the leaf outright."""
    assert TX.resolve("CLT") == "Mass Timber (CLT)"
    assert TX.resolve("cross laminated timber") == "Mass Timber (CLT)"


def test_something_outside_the_taxonomy_resolves_to_nothing():
    """Silently bucketing an unknown label would make the eval unfalsifiable."""
    assert TX.resolve("nonsense xyz") is None
    assert TX.resolve("") is None


def test_every_leaf_has_exactly_one_group():
    assert len(TX.leaves) == 18 and len(TX.groups) == 6
    assert all(TX.group_of[l] in TX.groups for l in TX.leaves)


def test_metal_building_is_left_unmapped_on_purpose():
    """958 facilities, 25% of everything Layer 3 classified. A pre-engineered metal building is an
    enclosure, not a component, so it fits none of the Other leaves — and forcing it into Light
    Gauge Steel would silently reclassify a quarter of the database on a guess."""
    assert "metal_building" in TX.unmapped_legacy
    assert "metal_building" not in TX.legacy


# ---- what the model is shown

def test_the_product_text_layer_3_never_saw_reaches_the_evidence():
    """871 source rows carry explicit product text and Layer 3's payload had no `notes` field."""
    ev = cap.evidence({"name": "Freres", "naics": "321213",
                       "notes": "source: APA | product_types: CLT | certification_program: APA"})
    assert "product_types: CLT" in ev and "naics: 321213" in ev


def test_evidence_is_only_ever_what_a_source_said():
    """No part of the payload is assembled here — it is source text or Layer 3's own judgement."""
    ev = cap.evidence({"name": "X", "product_type": "truss_component", "website": "x.example"})
    assert "layer3_type: truss_component" in ev and "website: x.example" in ev


def test_notes_that_say_nothing_about_a_product_are_left_out():
    ev = cap.evidence({"name": "X", "notes": "address_type: city_only | inspection_date: 2024"})
    assert "city_only" not in ev


# ---- the evaluation is the point, and it must refuse to flatter itself

def _fixed(leaf):
    return lambda f: (leaf, "fixed")


def test_accuracy_is_withheld_for_a_class_too_small_to_measure():
    """218 labels over 18 leaves is twelve apiece. Quoting 1.0 for a leaf with three examples
    would be inventing a measurement — which is what the control test was retired for."""
    labelled = [{"primary_capability": "Precast Concrete panel"}] * 3
    rep = cap.evaluate(TX, labelled, _fixed("Precast Concrete Panel"))
    leaf = rep["per_leaf"]["Precast Concrete Panel"]
    assert leaf["n"] == 3 and leaf["too_few_to_measure"] and leaf["accuracy"] is None


def test_group_and_leaf_are_scored_separately():
    """Predicting the right group and the wrong leaf is a real, partial success — Steel Volumetric
    called Wood Volumetric is nine of the sixteen steel rows in the keyword baseline."""
    labelled = [{"primary_capability": "Steel Volumetric Modular"}] * 4
    rep = cap.evaluate(TX, labelled, _fixed("Wood Volumetric Modular"))
    assert rep["group_accuracy"] == 1.0 and rep["leaf_accuracy_overall"] == 0.0


def test_a_label_that_does_not_resolve_is_reported_not_silently_dropped():
    rep = cap.evaluate(TX, [{"primary_capability": "Something Else Entirely"}], _fixed(None))
    assert rep["scored"] == 0 and rep["unresolved_labels"] == ["Something Else Entirely"]


def test_confusions_are_reported_so_a_failure_can_be_diagnosed():
    labelled = [{"primary_capability": "Steel Volumetric Modular"}] * 2
    rep = cap.evaluate(TX, labelled, _fixed("HUD Modular"))
    assert rep["top_confusions"][0] == {"truth": "Steel Volumetric Modular",
                                        "predicted": "HUD Modular", "n": 2}


# ---- the deterministic baseline exists so the model has something to beat

def test_the_signal_baseline_finds_a_leaf_a_source_named_outright():
    leaf, why = cap.signal_guess(TX, {"name": "Freres Lumber", "notes": "product_types: CLT"})
    assert leaf == "Mass Timber (CLT)" and "signal" in why


def test_the_baseline_says_nothing_rather_than_guessing():
    """A stage whose only number is its own accuracy cannot tell a good model from an easy task."""
    assert cap.signal_guess(TX, {"name": "Acme Holdings"})[0] is None


def test_the_assertions_name_the_taxonomy_version_and_the_evidence():
    a = cap.assertions_for("IC-1", "Mass Timber (CLT)", TX, 0.8, "source said CLT", "product_types: CLT")
    assert {x["field"] for x in a} == {"capability_group", "capability_leaf"}
    assert [x["value"] for x in a if x["field"] == "capability_group"] == ["Mass Timber"]
    assert all(f"taxonomy v{TX.version}" in x["evidence"] for x in a)
