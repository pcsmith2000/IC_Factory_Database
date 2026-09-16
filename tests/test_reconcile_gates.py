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
