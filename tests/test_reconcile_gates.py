import json
from pathlib import Path
from pipeline import reconcile, gates
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
