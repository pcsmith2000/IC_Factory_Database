"""Stage 15 classifies what a facility MAKES, over a taxonomy that lives in a file.

Layer 3 answers a different question — does this belong here at all — and its prompt hash is part
of the release tag. Keeping the two apart is why this stage exists separately: the taxonomy can
change without invalidating the classify cache or moving every tag.
"""
import json

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
    assert len(TX.leaves) == 19 and len(TX.groups) == 6
    assert all(TX.group_of[l] in TX.groups for l in TX.leaves)


def test_metal_building_is_a_leaf_of_its_own():
    """ADL ruled pre-engineered metal buildings in scope on 2026-09-21. The leaf is NOT Light Gauge
    Steel — the primary frame is hot-rolled — and it is not volumetric: a PEMB ships flat and is
    erected on site, which fails ADL's own test of "three-dimensional factory-built modules"."""
    assert TX.unmapped_legacy == []
    assert TX.resolve("PEMB") == "Pre-Engineered Metal Building"
    assert TX.group_of["Pre-Engineered Metal Building"] == "Other"
    # `legacy` reconciles vocabularies at the GROUP level and is not a mapping to this leaf:
    # steel-stud and metal-panel plants sit in the same Layer 3 bucket and belong elsewhere.
    assert TX.legacy["metal_building"] == "Other"


def test_a_trading_word_is_not_a_signal():
    """"Building systems" is what a PEMB manufacturer calls itself and also what a modular builder
    calls itself. As a signal it took three Wood Volumetric Modular plants off the floor."""
    assert "building systems" not in TX.signals["Pre-Engineered Metal Building"]


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


def test_which_source_spoke_is_part_of_the_evidence():
    """A plant on SIPA's member list makes structural insulated panels on that fact alone. The
    model is told which source said what, not just what was said."""
    ev = cap.evidence({"name": "X", "notes": "sipa: SIPA member types: Manufacturing"})
    assert "sipa" in ev and "Manufacturing" in ev


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


def test_every_leaf_carries_adls_definition():
    """The model is shown definitions, not just names. "Open" vs "Closed" and "panel" vs "module"
    are decided by a sentence; a leaf with no sentence is a leaf the classifier has to guess."""
    tx = cap.load()
    assert [l for l in tx.leaves if not tx.describe[l]] == []


def test_adls_own_short_spellings_resolve():
    """ADL's supply file writes the short forms. A spelling difference must never be scored as a
    disagreement — `Steel Structural Components` is 12 rows of the raw list."""
    tx = cap.load()
    assert tx.resolve("Steel Structural Components") == "Light Gauge Steel Structural Components"
    assert tx.resolve("MgO Panel") == "SIP / ICF (Other Composite Panel)"


def test_the_floor_does_not_move_when_the_file_is_reordered():
    """signal_guess is a floor, so it must measure the data and not the YAML. Ties break on the
    longest signal matched and then the leaf name — never on which group was typed first."""
    tx = cap.load()
    fac = {"name": "Example Panels", "notes": "product_types: structural insulated panel | wall panel"}
    first = cap.signal_guess(tx, fac)
    tx.signals = dict(reversed(list(tx.signals.items())))
    assert cap.signal_guess(tx, fac) == first


# ---- the evidence is the point of the stage, so what it keeps and drops is tested
def test_run_accounting_is_dropped_and_product_text_is_kept(tmp_path):
    """"135 kept of 5454 cards across 13 state page(s)" is the source's own bookkeeping, written
    once onto its first row. It says nothing about that plant. "site kind on page: Truss" does."""
    (tmp_path / "normalised").mkdir()
    (tmp_path / "normalised" / "s.csv").write_text(
        "source_id,notes,website,sq_ft,naics_verbatim,row_hash\n"
        "bldr,BFS branch type MF (manufacturing); site kind on page: Truss|135 kept of 5454 cards,"
        ",,321214,h1\n", encoding="utf-8")
    (tmp_path / "assertions.csv").write_text(
        "facility_id,row_hash\nIC-1,h1\n", encoding="utf-8")
    ev = cap.evidence_index(tmp_path)["IC-1"]
    assert "site kind on page: Truss" in ev["notes"]
    assert "5454" not in ev["notes"]
    assert ev["notes"].startswith("bldr:")          # which source said it stays attached


def test_a_facility_merges_every_source_that_spoke(tmp_path):
    """20% of facilities merge more than one source. Reading the merged record instead of one row
    at a time is the reason this stage is not just Layer 3 asked again."""
    (tmp_path / "normalised").mkdir()
    (tmp_path / "normalised" / "s.csv").write_text(
        "source_id,notes,website,sq_ft,naics_verbatim,row_hash\n"
        "sipa,SIPA member types: Manufacturing,,,321992,h1\n"
        "mbi,MBI membership type: Manufacturer/Direct,,,321992,h2\n", encoding="utf-8")
    (tmp_path / "assertions.csv").write_text(
        "facility_id,row_hash\nIC-1,h1\nIC-1,h1\nIC-1,h2\n", encoding="utf-8")
    notes = cap.evidence_index(tmp_path)["IC-1"]["notes"]
    assert "SIPA" in notes and "MBI" in notes
    assert notes.count("SIPA member types") == 1    # the repeated row_hash is counted once


# ---- the prompt is built from the taxonomy, so adding a leaf must reach the model
def test_the_prompt_carries_every_leaf_with_its_definition():
    tx = cap.load()
    p = cap.prompt_for(tx)
    for leaf in tx.leaves:
        assert leaf in p, leaf
        assert tx.describe[leaf][:40] in p, leaf


def test_the_prompt_hash_moves_when_the_taxonomy_does():
    """Layer 3's prompt hash is in the release tag and must not move. This one is separate
    precisely so a new leaf changes this stage and nothing else."""
    tx = cap.load()
    before = cap.prompt_hash(tx)
    tx.leaves = [l for l in tx.leaves if l != "3D Printing"]
    assert cap.prompt_hash(tx) != before


# ---- what the model is allowed to say
def test_an_answer_outside_the_taxonomy_is_dropped_not_mapped(monkeypatch):
    """The gate on this stage is "the value is a member of the taxonomy". A stage that also
    coerces an invented leaf into the nearest real one has no gate."""
    import json, pipeline

    class Msg:
        usage = None
        content = [type("B", (), {"type": "text", "text": json.dumps([
            {"i": 0, "leaf": "Wood Volumetric Modular", "confidence": 0.9, "reason": "wood"},
            {"i": 1, "leaf": "Steel Buildings Division", "confidence": 0.9, "reason": "invented"},
        ])})()]

    client = type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: Msg())})()})()
    monkeypatch.setattr(pipeline, "ai_client_and_model", lambda m: (client, m, "fake"))
    tx = cap.load()
    got = cap._call_once(tx, [{"name": "A"}, {"name": "B"}], "m", 0.0, False, None)
    assert set(got) == {0}


def test_an_unanswered_row_is_never_filled_from_the_floor():
    """signal_guess is the yardstick the model is measured against. Substituting it for a row the
    model failed to answer would fold the yardstick into the score."""
    import inspect
    src = inspect.getsource(cap.classify)
    assert "signal_guess" not in src.split('"""')[2]


def test_adls_prose_labels_still_resolve_loosely():
    """The strict path is for the model only. ADL's labels are prose written by people over
    several years and must keep resolving on a contained alias."""
    prose = "Wood Structural Components (trusses, joists and stairs)"
    assert TX.resolve(prose) == "Wood Structural Components (Trusses, etc.)"
    assert TX.resolve(prose, exact=True) is None
    # a spelling that IS listed resolves either way — the alias table is the contract
    assert TX.resolve("MgO Panel", exact=True) == "SIP / ICF (Other Composite Panel)"


def test_accounting_that_does_not_open_with_a_number_is_still_dropped(tmp_path):
    """"ADL list adl_4ward: 96 plants; 0 rows with no company" opens with a source name, not a
    number, and it was reaching the model as if it described the plant on that row."""
    (tmp_path / "normalised").mkdir()
    (tmp_path / "normalised" / "s.csv").write_text(
        "source_id,notes,website,sq_ft,naics_verbatim,row_hash\n"
        "adl,ADL list adl_4ward: 96 plants; 0 rows with no company,,,321214,h1\n"
        "adl,60000 SF factory + 200000 SF storage,,,321214,h2\n", encoding="utf-8")
    (tmp_path / "assertions.csv").write_text("facility_id,row_hash\nIC-1,h1\nIC-2,h2\n",
                                             encoding="utf-8")
    idx = cap.evidence_index(tmp_path)
    assert idx["IC-1"]["notes"] == ""
    # a number about THIS plant is not accounting and must survive
    assert "200000 SF storage" in idx["IC-2"]["notes"]


def test_the_token_ceiling_has_a_floor():
    """A per-row budget alone gave a 25-row batch 3,000 tokens. mercury-2.5 spent 2,930 of it on
    every attempt and returned truncated JSON, so the first real run answered 0 of 25."""
    import inspect
    src = inspect.getsource(cap._call_once)
    assert "max(16000" in src


def test_a_contract_error_says_what_came_back(monkeypatch):
    """"3 contract errors" and nothing else is unactionable — the cause was only visible in the
    token counts. The stop reason and a head of the text ride on the error."""
    import pipeline

    class Msg:
        usage = None
        stop_reason = "max_tokens"
        content = [type("B", (), {"type": "text", "text": '[{"i": 0, "leaf": "Wood Volu'})()]

    client = type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: Msg())})()})()
    monkeypatch.setattr(pipeline, "ai_client_and_model", lambda m: (client, m, "fake"))
    tx = cap.load()
    cap.classify(tx, [{"name": "A"}], stats=(st := {}))
    assert st["unanswered"] == 1
    detail = " ".join(st["contract_error_detail"])
    assert "max_tokens" in detail and "Wood Volu" in detail


def test_a_limited_run_is_a_sample_not_a_prefix(tmp_path):
    """facility_ids cluster by the source that issued them. The first 25 of 218 scored 0.44 on the
    floor against 0.417 for the whole set — a cheap run has to be comparable to a full one."""
    (tmp_path / "normalised").mkdir()
    (tmp_path / "assertions.csv").write_text("facility_id,row_hash\n", encoding="utf-8")
    rows = "\n".join(f"IC-{i:04d},Wood Volumetric Modular" for i in range(100))
    (tmp_path / "golden.csv").write_text("facility_id,primary_capability\n" + rows + "\n",
                                         encoding="utf-8")
    picked = [r["facility_id"] for r in cap.labelled_from(tmp_path, 10)]
    assert len(picked) == 10
    assert picked[0] == "IC-0000" and picked[-1] == "IC-0090"     # spans the whole range


def test_the_evidence_leads_with_what_a_source_said():
    """Rochester Homes carries "registered manufacturer, modular" AND naics 321991. Reading the
    code first, the model answered HUD Modular — it let a code beat a register that said otherwise
    in words. The source text now comes first in the payload."""
    ev = cap.evidence({"name": "Rochester Homes", "naics": "321991", "product_type": "hud_code",
                       "notes": "mo_psc: Missouri PSC registered manufacturer, modular"})
    assert ev.index("modular") < ev.index("321991")


def test_hud_needs_positive_evidence_of_the_federal_standard():
    """321991 is the manufactured-homes code and is assigned by convention to modular plants that
    build nothing to the HUD standard. Four of seven Modular rows in run 35637626925 were called
    HUD on that code alone."""
    p = cap.prompt_for(cap.load())
    assert "321991" in p and "POSITIVE evidence" in p
    assert "layer3_type" in p        # named as the weakest evidence, not as an answer


# ---- the production path: the facilities ADL never labelled
def test_predict_never_touches_a_facility_adl_labelled(tmp_path):
    """Survivorship ranks ADL's primary_capability above this stage, and the eval is scored
    against labels the stage is forbidden to replace. Asking the model about them anyway would
    spend tokens on an answer that can never be published."""
    (tmp_path / "normalised").mkdir()
    (tmp_path / "assertions.csv").write_text("facility_id,row_hash\n", encoding="utf-8")
    (tmp_path / "golden.csv").write_text(
        "facility_id,primary_capability\nIC-1,Wood Volumetric Modular\nIC-2,\nIC-3,   \n",
        encoding="utf-8")
    assert [r["facility_id"] for r in cap.unlabelled_from(tmp_path)] == ["IC-2", "IC-3"]
    assert [r["facility_id"] for r in cap.labelled_from(tmp_path)] == ["IC-1"]


def test_a_prediction_report_claims_no_accuracy():
    """These facilities have no label, so there is nothing to score against. A report that
    reached for an accuracy number here would be inventing a measurement."""
    tx = cap.load()
    rows = [{"facility_id": "IC-1", "name": "A", "notes": "sipa: SIPA member types: Manufacturing"}]
    rep = cap._predict_report(tx, rows, {"IC-1": {"leaf": "SIP / ICF (Other Composite Panel)",
                                                  "confidence": 0.9, "reason": "sipa member"}})
    assert "accuracy" not in json.dumps(rep)
    assert rep["by_group"] == {"Panel": 1}
    assert "Bathroom Pods" in rep["leaves_never_used"]
    assert rep["no_evidence_beyond_a_name"] == 0


# ---- the gate
def test_a_capability_outside_the_taxonomy_fails_the_run():
    """The taxonomy is a file, so a leaf renamed there must FAIL rather than silently orphan every
    value carrying the old name. And a stage that both validates and coerces has no validation."""
    from pipeline.enrich import gates
    good = [{"field": "capability_leaf", "value": "Mass Timber (CLT)"},
            {"field": "capability_group", "value": "Panel"}]
    assert gates.e9_every_capability_is_a_member_of_the_taxonomy(good).passed
    bad = good + [{"field": "capability_leaf", "value": "Steel Buildings Division"}]
    r = gates.e9_every_capability_is_a_member_of_the_taxonomy(bad)
    assert not r.passed and "Steel Buildings Division" in r.summary


def test_the_group_can_never_outrank_adls_own_label():
    """Stage 15 is SCORED against ADL's primary_capability. A stage that could overwrite its own
    answer key would make its measurement meaningless."""
    from pipeline import registry
    rules = registry.load_yaml("registry/survivorship.yaml")["fields"]
    for f in ("capability_group", "capability_leaf"):
        order = rules[f]["order"]
        assert order.index("class:D") < order.index("capability")


def test_the_stage_is_a_registered_source():
    """An assertion whose source_id is not in dim_source makes v_provenance answer "who says so"
    with a null join — the value is published and unattributable, which is the one thing this
    database is not allowed to do."""
    from pipeline import warehouse
    assert warehouse.SYNTHETIC_SOURCES[cap.SOURCE_ID]["class"] == "capability"
    assert "capability_group" in warehouse.GOLDEN_FIELDS
    assert "capability_leaf" in warehouse.GOLDEN_FIELDS
