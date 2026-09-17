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


def test_a_lead_is_not_a_found_plant():
    """A T0 row is a name with no location established.

    Counting one as found lets a roster of bare names lift recall while the database gains nothing
    anybody could visit — and a names-only roster is the cheapest source there is, which makes this
    the number most likely to be gamed by accident. On the warehouse as it stood when this was
    written, 18 of 29 matches were leads: recall read 12% and located recall 4.6%.
    """
    facs = [{"facility_id": "IC-1", "name": "Located Panel Works", "state": "OH", "tier": "T2"},
            {"facility_id": "IC-2", "name": "Lead Only Trusses", "state": "OH", "tier": "T0"}]
    control = [{"control_id": "1", "name": "Located Panel Works", "state": "OH"},
               {"control_id": "2", "name": "Lead Only Trusses", "state": "OH"}]
    r = measure.recall(control, facs, {})
    assert r["found"] == 2 and r["recall"] == 1.0
    assert r["found_located"] == 1 and r["found_lead_only"] == 1
    assert r["recall_located"] == 0.5


def test_the_located_split_is_reported_for_dev_and_sealed_too():
    facs = [{"facility_id": "IC-1", "name": "Real Plant Co", "state": "OH", "tier": "T1"}]
    control = [{"control_id": "1", "name": "Real Plant Co", "state": "OH", "split": "sealed"},
               {"control_id": "2", "name": "Absent Co", "state": "OH", "split": "dev"}]
    r = measure.recall(control, facs, {})
    assert r["sealed"]["recall_located"] == 1.0
    assert r["dev"]["recall_located"] == 0.0


def test_internal_spacing_is_not_evidence_of_a_different_company():
    """The control writes "Bankersteel" and "SR Sloan"; the sources write "Banker Steel" and
    "S R Sloan". Squashing is safe at the exact rungs because the whole name must still match."""
    facs = [{"facility_id": "IC-1", "name": "Banker Steel", "state": "FL", "tier": "T1"},
            {"facility_id": "IC-2", "name": "S R Sloan", "state": "VA", "tier": "T1"}]
    control = [{"control_id": "1", "name": "Bankersteel", "state": "FL"},
               {"control_id": "2", "name": "SR Sloan", "state": "VA"}]
    r = measure.recall(control, facs, {})
    assert r["found"] == 2 and r["by_method"] == {"name+state": 2}


def test_the_prefix_rung_still_needs_whole_words():
    """Squashing must not leak into the prefix rung, or "Blue" prefixes "Bluebird Panels"."""
    facs = [{"facility_id": "IC-1", "name": "Bluebird Panel Systems", "state": "OH", "tier": "T1"}]
    control = [{"control_id": "1", "name": "Blue Bird", "state": "OH"}]
    assert measure.recall(control, facs, {})["found"] == 0


def test_the_status_table_and_the_metric_cannot_disagree():
    """The table is built from the same matcher, because a hand-built copy drifts from it.

    The first version of this table reported nine plant-level gaps where the metric counted
    fourteen: it tested "is this company known" on the exact key while the metric tested it the way
    the rungs actually match.
    """
    facs = [{"facility_id": "IC-1", "name": "Real Plant Co", "state": "OH", "tier": "T2"},
            {"facility_id": "IC-2", "name": "Lead Co", "state": "OH", "tier": "T0"}]
    control = [{"control_id": "1", "name": "Real Plant Co", "state": "OH"},
               {"control_id": "2", "name": "Lead Co", "state": "OH"},
               {"control_id": "3", "name": "Nowhere Industries", "state": "OH"}]
    r = measure.recall(control, facs, {})
    t = measure.status_table(control, facs, {})
    assert sum(1 for x in t if x["status"] == "HAVE") == r["found"]
    assert sum(1 for x in t if x["has_address"].startswith("no")) == r["found_lead_only"]
    assert sum(1 for x in t if x["status"] == "MISSING") == r["n_missed"]
    assert [x["status"] for x in t] == ["HAVE", "HAVE", "MISSING"]
    assert t[1]["has_address"].startswith("no")      # a lead is on the list, without a street
    # Without the ingested index the table must not claim more than recall() can see. It knows the
    # plant is not published; it has checked nothing about what was fetched.
    assert t[2]["why_missing"] == "not in the published warehouse"


def test_a_missing_row_does_not_blame_the_classifier_for_a_plant_nobody_fetched(tmp_path):
    """"not in any source we hold" was written for 151 rows and checked for none of them.

    recall() only sees PUBLISHED facilities, so that sentence was a claim about ingestion made by
    code that had never read an ingested row. Against run 22's 103,017 normalised rows it was wrong
    for 29 of the 151: those plants were fetched and then lost, 9 of them labelled IC. The other
    122 really are in no source, and that is the difference between an edit that can work and one
    that cannot.
    """
    norm = tmp_path / "normalised"; norm.mkdir()
    (norm / "epa_frs.csv").write_text(
        "name_verbatim,row_hash\nLost Panel Systems Kokomo,h1\nSomething Else,h2\n")
    facs = [{"facility_id": "IC-1", "name": "Real Plant Co", "state": "OH", "tier": "T2"}]
    control = [{"control_id": "1", "name": "Real Plant Co", "state": "OH"},
               {"control_id": "2", "name": "Lost Panel Systems", "state": "IN"},
               {"control_id": "3", "name": "Nowhere Industries", "state": "OH"}]
    idx = measure.ingested_index(norm)
    t = measure.status_table(control, facs, {}, idx)

    # fetched by a source, absent from the warehouse: a leak, and the source is named
    assert t[1]["why_missing"].startswith("ingested but lost before publication")
    assert t[1]["ingested_by"] == "epa_frs"
    # no source holds it: no amount of prompt or matcher work reaches this row
    assert t[2]["why_missing"] == "never ingested — no source holds this name"
    assert t[2]["ingested_by"] == ""

    gap = measure.source_gap(t)
    assert (gap["found"], gap["ingested_but_lost"], gap["never_ingested"]) == (1, 1, 1)
    assert gap["ceiling"] == round(2 / 3, 4)      # what ALL pipeline work could reach, at most


def test_a_name_stripped_to_one_generic_word_still_matches():
    """norm_name removes "the" and "company", so "The Truss Company" becomes "truss".

    The exact rungs then cannot reach "trusssumner", and the prefix rung refuses a one-word key on
    purpose because "truss" would prefix half the industry. All five of that company's control rows
    were unmatchable while all eight of its plants sat in the warehouse with street addresses.
    """
    facs = [{"facility_id": f"IC-{i}", "name": f"The Truss Company - {city}", "state": st,
             "tier": "T2"}
            for i, (city, st) in enumerate([("Eugene", "OR"), ("Redmond", "OR"), ("Sumner", "WA")])]
    control = [{"control_id": "1", "name": "The Truss Company", "state": "OR"},
               {"control_id": "2", "name": "The Truss Company", "state": "OR"},
               {"control_id": "3", "name": "The Truss Company", "state": "WA"}]
    r = measure.recall(control, facs, {})
    assert r["found"] == 3 and r["by_method"] == {"name-prefix": 3}


def test_the_light_form_is_an_extra_rung_not_a_replacement():
    """Swapping the prefix rung over to the corporate-words-kept form wholesale cost two matches
    net against run 35255141179 — the stripped form wins where a corporate word is the only
    difference. Both are tried; neither is dropped."""
    stripped_only = [{"facility_id": "IC-1", "name": "ATCO Structures and Logistics", "tier": "T1"}]
    assert measure.recall([{"control_id": "1", "name": "ATCO Structures and Logistics (USA) Inc"}],
                          stripped_only, {})["found"] == 1
    light_only = [{"facility_id": "IC-2", "name": "The Truss Company - Pasco", "tier": "T1"}]
    assert measure.recall([{"control_id": "1", "name": "The Truss Company"}],
                          light_only, {})["found"] == 1
