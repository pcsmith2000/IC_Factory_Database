"""Recall against the verified control list.

The list is a set of companies a human checked. The question it answers is "is this name in the
database" — so a bare name list has to work. Requiring a state made one score 0%, which is
indistinguishable from the pipeline having missed every single one, and that false zero is the
same failure `recall: 0.0` was reporting on every release before 2026-09-17.
"""
from pipeline import measure


FACS = [
    {"facility_id": "IC-1", "name": "DEER VALLEY HOMEBUILDERS", "state": "AL"},
    {"facility_id": "IC-2", "name": "Cavco Industries", "state": "TX"},
    {"facility_id": "IC-3", "name": "Cavco Industries", "state": "AZ"},
]


def test_a_bare_name_list_works_with_no_state_and_no_triage():
    """The whole point: names only, nothing else."""
    control = [{"control_id": "1", "name": "Deer Valley Homebuilders"},
               {"control_id": "2", "name": "Nowhere Modular Inc"}]
    r = measure.recall(control, FACS, {})
    assert r["tested"] and r["in_scope"] == 2
    assert r["found"] == 1 and r["recall"] == 0.5
    assert r["by_method"] == {"name": 1}
    assert r["untriaged_assumed_in_scope"] == 2      # reported, not hidden
    assert r["missed"] == ["Nowhere Modular Inc"]


def test_state_is_used_when_the_list_has_one():
    control = [{"control_id": "1", "name": "Cavco Industries", "state": "az", "triage": "in_scope_locatable"}]
    assert measure.recall(control, FACS, {})["by_method"] == {"name+state": 1}


def test_a_name_in_several_states_is_found_once():
    control = [{"control_id": "1", "name": "Cavco Industries"}]
    r = measure.recall(control, FACS, {})
    assert r["found"] == 1 and r["by_method"] == {"name": 1}


def test_a_plant_is_claimed_by_one_control_row_only():
    """The control list is plant-level: Builders FirstSource is 21 rows in 13 states. Counting
    every one of them as found against a single warehouse row read 100% for a company we hold
    one plant of."""
    control = [{"control_id": "1", "name": "Cavco Industries", "state": "TX"},
               {"control_id": "2", "name": "Cavco Industries", "state": "TX"}]
    r = measure.recall(control, FACS, {})
    assert r["found"] == 1 and r["recall"] == 0.5
    assert r["company_present_plant_missing"] == 1    # not a plain miss: the company IS known
    assert r["missed"] == []


def test_city_outranks_state_across_the_whole_list():
    """Rungs are walked list-wide, not row by row, so the row that names the city takes the plant
    before a row with only a state can claim it."""
    facs = [{"facility_id": "IC-9", "name": "Truss Co", "state": "OR", "city_norm": "eugene"}]
    control = [{"control_id": "1", "name": "Truss Co", "state": "OR"},
               {"control_id": "2", "name": "Truss Co", "state": "OR", "city": "Eugene"}]
    r = measure.recall(control, facs, {})
    assert r["by_method"] == {"name+city": 1}


def test_a_trading_name_matches_the_registered_name_within_a_state():
    """"Fading West" is the control list's name for FADING WEST BUILDING SYSTEMS, LLC."""
    facs = [{"facility_id": "IC-9", "name": "FADING WEST BUILDING SYSTEMS, LLC", "state": "CO"}]
    control = [{"control_id": "1", "name": "Fading West", "state": "CO"}]
    assert measure.recall(control, facs, {})["by_method"] == {"name-prefix": 1}


def test_a_prefix_match_does_not_cross_states():
    """84 Lumber has a plant in most states; the Bessemer AL door shop is not the Virginia one."""
    facs = [{"facility_id": "IC-9", "name": "84 Lumber Door Shop - Bessemer", "state": "AL"}]
    control = [{"control_id": "1", "name": "84 Lumber", "state": "VA"}]
    assert measure.recall(control, facs, {})["found"] == 0


def test_a_one_word_prefix_is_not_distinctive_enough():
    """"Blue Company" normalises to "blue" and prefix-matched "Blue Horse Building"."""
    facs = [{"facility_id": "IC-9", "name": "Blue Company", "state": "NC"}]
    control = [{"control_id": "1", "name": "Blue Horse Building", "state": "NC"}]
    assert measure.recall(control, facs, {})["found"] == 0


def test_crosswalk_beats_name_matching():
    control = [{"control_id": "c9", "name": "A Name That Matches Nothing"}]
    r = measure.recall(control, FACS, {"c9": "IC-2"})
    assert r["found"] == 1 and r["by_method"] == {"crosswalk": 1}


def test_out_of_scope_rows_are_excluded_but_blank_triage_is_not():
    control = [{"control_id": "1", "name": "Deer Valley Homebuilders", "triage": "out_of_scope"},
               {"control_id": "2", "name": "Cavco Industries", "triage": ""}]
    r = measure.recall(control, FACS, {})
    assert r["in_scope"] == 1 and r["untriaged_assumed_in_scope"] == 1


def test_an_empty_control_file_is_untested_not_zero():
    r = measure.recall([], FACS, {})
    assert r["tested"] is False and r["recall"] is None
