import pytest
import json
from pathlib import Path
from pipeline import reconcile, gates, classify
from pipeline.contract import COLUMNS, normalise

def _row(sid, name, addr, city, st, **kw):
    r = {c: "" for c in COLUMNS}
    r.update(source_id=sid, source_url="u", source_document="d", retrieved_date="2026-09-14", row_position="1",
             status_basis="on_current_list", name_verbatim=name, address_verbatim=addr, city_verbatim=city, state_verbatim=st)
    r.update(kw); return r

ROWS = [
    _row("pa", "Legacy Building Solutions", "19500 County Rd 142", "Maine Prairie", "MN"),
    _row("iibc", "LEGACY BUILDING SOLUTIONS INC", "19500 County Road 142", "Saint Augusta", "MN"),
    _row("tx", "Aura Prefab, LLC", "5730 Clinton Dr", "Houston", "TX"),
    _row("mi", "PAR-KUT INTERNATIONAL", "40961 Production Dr", "Harrison Township", "MI"),
    _row("mi", "MARDAN FABRICATION", "40961 Production Dr", "Harrison Township", "MI"),
]

def test_street_key_clusters_across_city_spelling_and_ids_are_stable(tmp_path: Path):
    reg = tmp_path / "id_registry.json"
    a = reconcile.run(normalise(ROWS), reg)
    assert a["n_facilities"] == 3                      # Legacy ×2 → 1, Aura, PAR-KUT+MARDAN share a street (G1's job)
    first = json.loads(reg.read_text())
    b = reconcile.run(normalise(ROWS), reg)
    assert b["ids_issued"] == 0 and json.loads(reg.read_text()) == first
    assert gates.g3_id_stability(b["ids_issued"], 0, is_rerun=True).passed

def test_g1_reports_and_never_merges(tmp_path: Path):
    rec = reconcile.run(normalise(ROWS), tmp_path / "ids.json")
    res = gates.g1_dedupe(rec["facilities"], 0.02, {"street_key": .95, "name_city": .90, "fuzzy": .80}, tmp_path / "pairs.csv")
    assert (tmp_path / "pairs.csv").exists()
    assert rec["n_facilities"] == 3                    # G1 changed nothing

def test_g2_flags_two_streets_in_one_cluster():
    rows = [{"facility_id": "IC-1", "street_key": "1 a st"}, {"facility_id": "IC-1", "street_key": "2 b st"}]
    assert not gates.g2_false_merge(rows).passed
    assert gates.g2_false_merge(rows[:1]).passed

def test_g5_scores_seeds():
    seeds = [{"row_hash": "a", "seed_label": "IC"}, {"row_hash": "b", "seed_label": "NOT-IC"}, {"row_hash": "c", "seed_label": "IC"}]
    labels = {"a": {"label": "IC"}, "b": {"label": "NOT-IC"}, "c": {"label": "NOT-IC"}}
    r = gates.g5_classifier_eval(labels, seeds, 0.95, 0.90)
    assert r.details["precision"] == 1.0 and r.details["recall"] == 0.5 and not r.passed
    assert not gates.g5_classifier_eval({}, [], 0.95, 0.90).passed   # unaudited AI step fails


# Layer 3 candidate generation. Deterministic, runs with the classifier off, and decides what the
# model is ever allowed to see — a row dropped here is never judged, only silently absent.

CORE = {"321991", "321992", "332311", "321214"}
_cand = lambda naics, name: classify.candidates(
    [{"naics_verbatim": naics, "name_verbatim": name}], CORE)

def test_wide_families_are_candidates_whatever_the_row_is_called():
    # Real plants on the validated list, all in 3219/3212, none saying so in its name. Before the
    # widening every one of these was dropped before the classifier (docs/epa-coverage.md).
    for naics, name in (("321211", "SHELTER SYSTEMS"), ("321911", "TOLL INTEGRATED SYSTEMS"),
                        ("321918", "317715012 - PACIFIC WALL SYSTEMS INC")):
        got = _cand(naics, name)
        assert got and got[0]["_candidate_reason"].startswith("wide family"), name

def test_truss_pairs_with_2362_but_the_placename_guard_still_holds():
    # Truss plants are routinely coded to building construction, not manufacturing. Pairing the
    # keyword with 2362 recovers 12 of them from the EPA slice — including a validated company —
    # while the guard keeps the town of Trussville, AL out of all 44 rows that carry its name.
    assert _cand("236210", "NORTH GEORGIA TRUSS SYSTEMS")[0]["_candidate_reason"].endswith("× 2362")
    assert _cand("236220", "MAGBEE TRUSS PLANT")[0]["_candidate_reason"].endswith("× 2362")
    assert _cand("236220", "TRUSSVILLE LIBRARY") == []

def test_product_name_keywords_reach_3323():
    # Pre-engineered metal building makers call themselves "building systems" and sit in 3323,
    # which no other rule reaches by name.
    assert _cand("332312", "CECO BUILDING SYSTEMS")[0]["_candidate_reason"].endswith("× 3323")
    assert _cand("332321", "MIAMI WALL SYSTEMS, INC.")[0]["_candidate_reason"].endswith("× 3323")

def test_what_neither_option_reaches():
    # The honest limit. Banker Steel is a real plant on the validated list, in EPA, coded 332312,
    # and carries no keyword at all — no name rule reaches it, and it is outside 3219/3212.
    # Only classifying the whole slice would put it in front of the model.
    assert _cand("332312", "BANKER STEEL - ORLANDO") == []
    assert _cand("423310", "84 LUMBER COMPANY") == []

def test_core_and_keyword_paths_still_hold():
    assert _cand("321992", "ANY NAME AT ALL")[0]["_candidate_reason"] == "core naics 321992"
    assert _cand("327390", "ACME PRECAST CONCRETE")[0]["_candidate_reason"].endswith("× 3273")
    assert _cand("332312", "PLAIN STEEL CO") == []


# Layer 3 output contract. A run is ~131 batches, so anything that fails one batch fails the run.

def test_salvages_objects_when_one_reason_breaks_the_array():
    # The shape that killed CI run 35154164293: an unescaped quote inside one `reason`. The array
    # will not parse, but the objects either side of it are valid JSON.
    bad = ('[{"i":0,"label":"IC","confidence":0.9,"type":"panel","reason":"panel plant"},'
           ' {"i":1,"label":"NOT-IC","confidence":0.8,"type":"none","reason":"makes 2" pipe"},'
           ' {"i":2,"label":"IC","confidence":0.9,"type":"truss_component","reason":"truss"}]')
    import json as _json
    with pytest.raises(_json.JSONDecodeError):
        _json.loads(bad)
    assert [o["i"] for o in classify._objects(bad)] == [0, 2]

def test_objects_handles_clean_prose_and_empty():
    assert classify._objects('[{"i":0,"label":"IC"}]') == [{"i": 0, "label": "IC"}]
    assert classify._objects("I cannot help with that.") == []

def test_batch_reasks_only_the_rows_that_came_back_broken(monkeypatch):
    rows = [{"row_hash": f"h{i}", "name_verbatim": f"PLANT {i}"} for i in range(3)]
    calls = []

    def fake(sub, prompt, model, temperature, usage_out, repair):
        calls.append((len(sub), repair))
        if len(calls) == 1:                      # row 1 garbled, 0 and 2 fine
            return {0: {"i": 0, "label": "IC"}, 2: {"i": 2, "label": "NOT-IC"}}
        return {0: {"i": 0, "label": "UNCERTAIN"}}

    monkeypatch.setattr(classify, "_call_once", fake)
    out = classify.classify_batch(rows, "p", "m", 0)
    assert [o["row_hash"] for o in out] == ["h0", "h1", "h2"]
    assert out[1]["label"] == "UNCERTAIN"        # the re-ask filled the hole
    assert calls == [(3, False), (1, True)]      # second call carried only the missing row

def test_batch_raises_rather_than_leave_a_row_unlabelled(monkeypatch):
    # A row the model never labels must not pass silently: run.py reads a missing label as NOT-IC
    # and drops the establishment, so swallowing this would quietly shrink the dataset.
    def never(sub, prompt, model, temperature, usage_out, repair):
        raise classify.BatchContractError("model refused")

    monkeypatch.setattr(classify, "_call_once", never)
    rows = [{"row_hash": f"h{i}", "name_verbatim": "X"} for i in range(2)]
    with pytest.raises(RuntimeError, match="2 of 2 rows still unlabelled"):
        classify.classify_batch(rows, "p", "m", 0)

def test_retries_raise_the_temperature(monkeypatch):
    # At temperature 0 the same question returns the same broken answer, so an identical retry is
    # not a retry at all.
    temps = []

    def fake(sub, prompt, model, temperature, usage_out, repair):
        temps.append(temperature)
        if len(temps) < 3:
            raise classify.BatchContractError("no usable JSON objects in response")
        return {0: {"i": 0, "label": "IC"}}

    monkeypatch.setattr(classify, "_call_once", fake)
    classify.classify_batch([{"row_hash": "h0", "name_verbatim": "X"}], "p", "m", 0)
    assert temps == [0, 0.2, 0.4]


def test_a_seed_that_is_also_a_candidate_is_classified_once(monkeypatch, tmp_path):
    # Run 35156659530 classified all 60 seeds twice — once as candidates, once as seeds — and
    # 7 came back with different labels on the two passes. G5 then scored 93% or 89% on the same
    # run depending only on which copy landed last. A gate whose verdict depends on batch ordering
    # is not measuring the model.
    shared = [{"row_hash": f"h{i}", "name_verbatim": f"PLANT {i}"} for i in range(4)]
    seeds = [dict(r, seed_label="IC") for r in shared[:2]]     # two rows are BOTH
    seen: list[str] = []

    def fake(sub, prompt, model, temperature, usage_out, repair):
        seen.extend(r["row_hash"] for r in sub)
        return {i: {"i": i, "label": "IC"} for i in range(len(sub))}

    monkeypatch.setattr(classify, "_call_once", fake)
    monkeypatch.setattr(classify, "ai_client_and_model", lambda m: (None, m, "test"))
    prompt = tmp_path / "p.md"; prompt.write_text("p")
    meta = classify.run(shared, {"model": "m", "temperature": 0, "batch_size": 100},
                        seeds, tmp_path / "cache", prompt)
    assert sorted(seen) == ["h0", "h1", "h2", "h3"]      # each row exactly once
    assert len(seen) == len(set(seen))
    assert meta["seeds_also_candidates"] == 2
    assert set(meta["labels"]) == {"h0", "h1", "h2", "h3"}


def test_recovers_the_unquoted_dialect_that_killed_run_8():
    # amazon/nova-lite answered run 35162970190 in a JavaScript object dialect — every judgement
    # correct, not a quote in sight — and json.loads rejected all 101 rows in the batch, three
    # attempts running, killing the run. The instruction that provoked it ("use no double quotes
    # inside any string value", meant for gpt-oss-120b's unescaped quotes) is fixed in the user
    # message; this is the net under it.
    real = ('[{i: 0, label: NOT-IC, confidence: 0.9, type: none, reason: pallet systems}, '
            '{i: 2, label: IC, confidence: 0.95, type: truss_component, reason: roof truss plant}]')
    got = classify._objects(real)
    assert [o["i"] for o in got] == [0, 2]
    assert got[0]["label"] == "NOT-IC" and got[1]["label"] == "IC"
    # numbers must not be turned into strings by the repair
    assert got[0]["confidence"] == 0.9 and not isinstance(got[0]["confidence"], str)

def test_relaxed_parsing_never_touches_real_json():
    strict = '[{"i":0,"label":"IC","confidence":1,"type":"panel","reason":"x"}]'
    assert classify._objects(strict) == [{"i": 0, "label": "IC", "confidence": 1,
                                          "type": "panel", "reason": "x"}]
    assert classify._relaxed(strict) == []      # declines anything already quoted
    assert classify._objects("I cannot help with that.") == []


def test_reads_string_indices_and_label_case(monkeypatch, tmp_path):
    # Run 35164672039 (openai/gpt-oss-20b) returned 100 well-formed objects per batch and every
    # one was rejected: `isinstance(i, int)` is False for "0", and "not-ic" is not in LABELS. The
    # judgements were all present and all discarded over formatting, three batches running.
    assert classify._index({"i": 0}) == 0
    assert classify._index({"i": "3"}) == 3
    assert classify._index({"index": "7"}) == 7
    assert classify._index({"i": True}) is None          # a bool is not an index
    assert classify._index({"i": "x"}) is None
    assert classify._label({"label": "not-ic"}) == "NOT-IC"
    assert classify._label({"label": " IC "}) == "IC"
    assert classify._label({"label": "NOT_IC"}) == "NOT-IC"
    assert classify._label({"label": "MAYBE"}) is None   # still rejected
    assert classify._label({"label": 3}) is None

def test_unusable_batch_error_shows_an_object(monkeypatch):
    # The old message said only "none with a usable index and label", which could not distinguish
    # bad JSON from good JSON that failed validation. That cost a log dive.
    calls = {}

    class FakeMsg:
        content = [type("B", (), {"type": "text", "text": '[{"i":"zz","label":"NOPE"}]'})()]
        usage = None
        stop_reason = "end_turn"

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                calls["n"] = calls.get("n", 0) + 1
                return FakeMsg()

    monkeypatch.setattr(classify, "ai_client_and_model", lambda m: (FakeClient(), m, "test"))
    with pytest.raises(classify.BatchContractError, match="first object"):
        classify._call_once([{"row_hash": "h0", "name_verbatim": "X"}], "p", "m", 0, None, False)


def test_g5_reports_precision_at_the_production_base_rate():
    # 30/30 seeds flatter precision: false positives come from the negative class, which is four
    # times larger at a 20% base rate. Measured tonight, a 93% seed precision implies ~77% real.
    seeds = ([{"row_hash": f"p{i}", "seed_label": "IC"} for i in range(30)]
             + [{"row_hash": f"n{i}", "seed_label": "NOT-IC"} for i in range(30)])
    labels = {s["row_hash"]: {"label": s["seed_label"]} for s in seeds}
    for i in range(2):                                    # two false positives -> 93% precision
        labels[f"n{i}"] = {"label": "IC"}
    r = gates.g5_classifier_eval(labels, seeds, 0.95, 0.90, base_rate=0.20)
    assert round(r.details["precision"], 2) == 0.94
    assert r.details["precision_at_base_rate"] < r.details["precision"]
    assert round(r.details["precision_at_base_rate"], 2) == 0.79
    assert "base rate" in r.summary
    # It only gates when a threshold is set.
    assert not gates.g5_classifier_eval(labels, seeds, 0.90, 0.90, 0.20, 0.95).passed
    assert gates.g5_classifier_eval(labels, seeds, 0.90, 0.90, 0.20, None).passed

def test_g2_and_g3_say_untested_rather_than_passing():
    # Across 27 run records these passed 14 times each having asserted nothing.
    single = [{"facility_id": "IC-1", "street_key": "1 a st"},
              {"facility_id": "IC-2", "street_key": "2 b st"}]
    r = gates.g2_false_merge(single)
    assert r.passed and not r.tested and "untested" in r.summary

    merged_clean = [{"facility_id": "IC-1", "street_key": "1 a st"},
                    {"facility_id": "IC-1", "street_key": "1 a st"}]
    r = gates.g2_false_merge(merged_clean)
    assert r.passed and r.tested          # a real merge with one street key IS a real pass

    bad = [{"facility_id": "IC-1", "street_key": "1 a st"},
           {"facility_id": "IC-1", "street_key": "2 b st"}]
    assert not gates.g2_false_merge(bad).passed

    r = gates.g3_id_stability(160, 0, is_rerun=False)
    assert r.passed and not r.tested and "untested" in r.summary
    assert gates.g3_id_stability(0, 0, is_rerun=True).tested

def test_g1_says_when_it_is_only_passing_on_a_relaxed_ceiling(tmp_path):
    rows = normalise(ROWS)
    rec = reconcile.run(rows, tmp_path / "ids.json")
    r = gates.g1_dedupe(rec["facilities"], 0.10, {"street_key": .95, "name_city": .90, "fuzzy": .80},
                        tmp_path / "p.csv", target_rate=0.02)
    if r.details["rate"] > 0.02:
        assert r.passed and r.details["would_fail_target"] and "ABOVE the 2% target" in r.summary


# ---------------------------------------------------------------- G3, the gate that never fired
def test_g3_passes_a_rerun_that_issues_no_ids():
    """The only outcome G3 has ever reported is 'untested'. This is what a real pass looks like."""
    r = gates.g3_id_stability(ids_issued=0, allow_new=0, is_rerun=True)
    assert r.passed and r.tested
    assert "0 new ids" in r.summary


def test_g3_fails_a_rerun_that_renumbers():
    """The failure this gate exists for: identical inputs, yet the registry issued fresh ids —
    a signature changed, so facilities silently renumbered and every downstream id broke."""
    r = gates.g3_id_stability(ids_issued=1, allow_new=0, is_rerun=True)
    assert not r.passed and r.tested
    assert "1 new ids" in r.summary


def test_g3_honours_a_nonzero_allowance():
    assert gates.g3_id_stability(ids_issued=2, allow_new=2, is_rerun=True).passed
    assert not gates.g3_id_stability(ids_issued=3, allow_new=2, is_rerun=True).passed


def test_g3_on_a_first_run_is_untested_and_says_so():
    """A first run cannot observe stability. It must not report a pass that means nothing."""
    r = gates.g3_id_stability(ids_issued=500, allow_new=0, is_rerun=False)
    assert r.tested is False
    assert "untested" in r.summary and "--rerun" in r.summary


def test_id_registry_never_renumbers_an_existing_signature(tmp_path: Path):
    """G3's premise: a signature seen before keeps its id, even as new ones are issued around it."""
    p = tmp_path / "ids.json"
    reg = reconcile.IdRegistry(p)
    first = reg.get("FL|S|123main")
    reg.get("AL|S|9oak")
    reg.save()

    reopened = reconcile.IdRegistry(p)          # a later run, same registry on disk
    assert reopened.get("FL|S|123main") == first
    assert reopened.issued_this_run == 0        # nothing new — this is what a re-run must show
    assert reopened.get("TX|S|5elm") != first   # a genuinely new signature still gets a new id
    assert reopened.issued_this_run == 1


# ------------------------------------------------- G1 and the multi-plant company
def _fac(fid, name, city, street, state="TX", tier="T1"):
    return {"facility_id": fid, "name": name, "city_norm": city, "street_key": street,
            "state": state, "tier": tier}


def test_g1_does_not_collapse_two_sites_of_one_company_in_one_city(tmp_path: Path):
    """TAS Energy has five Houston plants; TXLA Systems five in Huffman. Same name, same city,
    different street — different establishments. Every one of the 58 'duplicate' pairs among
    located facilities in the 2026-09-17 run was this shape, and all 58 were false."""
    facs = [_fac("IC-1", "TAS ENERGY INC.", "houston", "9450 w wingfoot rd"),
            _fac("IC-2", "TAS ENERGY INC.", "houston", "2920 airport blvd")]
    r = gates.g1_dedupe(facs, 0.10, {"street_key": 0.95, "name_city": 0.90, "fuzzy": 0.80},
                        tmp_path / "audit.csv", target_rate=0.02)
    assert r.details["collapses"] == 0
    assert r.details["rate"] == 0.0
    # still surfaced for review, just below the collapse threshold
    assert r.details["pairs"] == 1
    assert "DIFFERENT street" in (tmp_path / "audit.csv").read_text()


def test_g1_still_collapses_a_real_duplicate_with_no_address(tmp_path: Path):
    """The fix must not blind the gate: same name and city with no street on either side is
    still the duplicate it always was."""
    facs = [_fac("IC-1", "COZY CABINS LLC", "new holland", "", tier="T0"),
            _fac("IC-2", "Cozy Cabins", "new holland", "", tier="T0")]
    r = gates.g1_dedupe(facs, 0.10, {"street_key": 0.95, "name_city": 0.90, "fuzzy": 0.80},
                        tmp_path / "a.csv", target_rate=0.02)
    assert r.details["collapses"] == 1


def test_g1_still_collapses_the_same_street_seen_twice(tmp_path: Path):
    facs = [_fac("IC-1", "MODULAR BUILDERS", "rochester", "3089 ft wayne rd"),
            _fac("IC-2", "Modular Builders Inc", "rochester", "3089 ft wayne rd")]
    r = gates.g1_dedupe(facs, 0.10, {"street_key": 0.95, "name_city": 0.90, "fuzzy": 0.80},
                        tmp_path / "a.csv", target_rate=0.02)
    assert r.details["collapses"] == 1


def test_g1_still_merges_an_addressless_row_into_an_addressed_one(tmp_path: Path):
    """One side knows the street, the other only the city. That is one plant, and the gate
    must keep saying so — this is the shape the addressless sources produce."""
    facs = [_fac("IC-1", "DEER RUN CABINS", "campbellsville", "100 main st"),
            _fac("IC-2", "Deer Run Cabins", "campbellsville", "", tier="T0")]
    r = gates.g1_dedupe(facs, 0.10, {"street_key": 0.95, "name_city": 0.90, "fuzzy": 0.80},
                        tmp_path / "a.csv", target_rate=0.02)
    assert r.details["collapses"] == 1


# ------------------------------------------------- attaching addressless rows to a known plant
def _row(name, city, state="KY", street="", src="epa_frs", rh=None):
    return {"name_verbatim": name, "city_norm": city, "state": state, "street_key": street,
            "source_id": src, "row_hash": rh or f"{name}{city}{street}{src}",
            "address_verbatim": street, "retrieved_date": "2026-09-17"}


def test_addressless_row_joins_the_one_plant_it_can_only_be(tmp_path: Path):
    """One roster has the street, another only city+state. Same plant, and it must take one id."""
    rows = [_row("DEER RUN CABINS", "campbellsville", street="100 main st"),
            _row("Deer Run Cabins", "campbellsville", src="iibc")]
    res = reconcile.run(rows, tmp_path / "ids.json")
    assert res["n_facilities"] == 1
    assert len({r["facility_id"] for r in rows}) == 1
    fac = res["facilities"][0]
    assert fac["n_sources"] == 2          # the point: corroboration, not just fewer rows
    assert fac["tier"] == "T2"            # two sources with an address between them


def test_addressless_row_stays_put_when_two_plants_could_claim_it(tmp_path: Path):
    """TAS Energy has five Houston plants. An addressless 'TAS Energy, Houston' row cannot be
    assigned to one of them, and guessing would be a false merge — the failure G2 exists for."""
    rows = [_row("TAS ENERGY INC.", "houston", "TX", "9450 w wingfoot rd"),
            _row("TAS ENERGY INC.", "houston", "TX", "2920 airport blvd"),
            _row("Tas Energy Inc", "houston", "TX", src="iibc")]
    res = reconcile.run(rows, tmp_path / "ids.json")
    assert res["n_facilities"] == 3       # the addressless row keeps its own id


def test_addressless_row_in_another_city_is_not_attached(tmp_path: Path):
    rows = [_row("ACME MODULAR", "louisville", "KY", "100 main st"),
            _row("ACME MODULAR", "lexington", "KY", src="iibc")]
    assert reconcile.run(rows, tmp_path / "ids.json")["n_facilities"] == 2


def test_attaching_issues_no_new_ids_so_g3_is_unaffected(tmp_path: Path):
    """The addressed cluster keeps its signature and its id; the addressless one is retired."""
    p = tmp_path / "ids.json"
    first = reconcile.run([_row("DEER RUN CABINS", "campbellsville", street="100 main st")], p)
    fid = first["facilities"][0]["facility_id"]
    second = reconcile.run([_row("DEER RUN CABINS", "campbellsville", street="100 main st"),
                            _row("Deer Run Cabins", "campbellsville", src="iibc")], p)
    assert second["ids_issued"] == 0
    assert second["facilities"][0]["facility_id"] == fid


def test_an_attached_row_does_not_claim_street_level_provenance(tmp_path: Path):
    """The fold gives the row a new facility id, not a street address it never had."""
    addressed = _row("DEER RUN CABINS", "campbellsville", street="100 main st")
    homeless = _row("Deer Run Cabins", "campbellsville", src="iibc")
    reconcile.run([addressed, homeless], tmp_path / "ids.json")
    assert addressed["match_method"] == "street_key" and addressed["match_confidence"] == 0.90
    assert homeless["match_method"] == "name+city→addressed"
    assert homeless["match_confidence"] == 0.70      # not the cluster's 0.90
