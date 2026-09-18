from pipeline.classify import _anchor


def _rows(*names):
    return [{"row_hash": f"h{k}", "name_verbatim": n} for k, n in enumerate(names)]


def test_a_skipped_row_no_longer_shifts_every_label_after_it():
    """Run 35272678348, batch 042608d2: the model skipped a row and counted on by itself, so
    Formetco carried Roseburg's reason, Pallet One carried Formetco's ("makes metal buildings" -> IC)
    and Cavco carried Pallet One's ("Pallets are excluded" -> NOT-IC, a G5 seed)."""
    rows = _rows("ROSEBURG FOREST PRODUCTS CO.", "FORMETCO INC.", "PALLET ONE OF ALABAMA",
                 "317714609 - CAVCO INDUSTRIES INC")
    got = [{"i": 0, "name": "FORMETCO INC.", "label": "IC", "reason": "Formetco makes metal buildings"},
           {"i": 1, "name": "PALLET ONE OF ALABAMA", "label": "NOT-IC", "reason": "Pallets are excluded"},
           {"i": 2, "name": "317714609 - CAVCO INDUSTRIES INC", "label": "IC", "reason": "core home code"}]
    stats = {}
    out = _anchor(got, rows, stats)
    assert {i: o["label"] for i, o in out.items()} == {1: "IC", 2: "NOT-IC", 3: "IC"}
    assert stats == {"realigned": 3}
    assert 0 not in out  # Roseburg came back with nothing of its own, so the re-ask covers it


def test_the_ordinary_case_is_unchanged_and_a_name_that_matches_nothing_is_dropped():
    rows = _rows("A CO", "B CO", "C CO")
    got = [{"i": 0, "name": "a co", "label": "IC"}, {"i": 1, "name": "B, Co.", "label": "NOT-IC"},
           {"i": 2, "name": "ZZZ INDUSTRIES", "label": "IC"}]
    stats = {}
    out = _anchor(got, rows, stats)
    assert {i: o["label"] for i, o in out.items()} == {0: "IC", 1: "NOT-IC"}
    assert stats == {"misanchored": 1}


def test_an_object_without_a_name_is_taken_at_its_index_as_before():
    rows = _rows("A CO", "B CO")
    out = _anchor([{"i": 1, "label": "IC"}, {"index": "0", "label": "UNCERTAIN"}], rows, stats := {})
    assert {i: o["label"] for i, o in out.items()} == {1: "IC", 0: "UNCERTAIN"}
    assert stats == {"unanchored": 2}


def test_a_duplicate_name_can_only_anchor_at_its_own_index():
    rows = _rows("CLARK PACIFIC", "CLARK PACIFIC", "OTHER")
    got = [{"i": 0, "name": "CLARK PACIFIC", "label": "IC"},
           {"i": 2, "name": "CLARK PACIFIC", "label": "NOT-IC"}]  # says Clark, sits on OTHER: ambiguous
    out = _anchor(got, rows, stats := {})
    assert {i: o["label"] for i, o in out.items()} == {0: "IC"}
    assert stats == {"misanchored": 1}
