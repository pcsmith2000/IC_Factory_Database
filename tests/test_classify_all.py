from pipeline.classify import candidates


def test_a_registry_row_with_no_naics_is_a_candidate_only_when_its_source_asks():
    """GA DCA's rows carry no NAICS, so the keyword x NAICS tests never admit them, and a needs_classify
    row that is never candidated is dropped as if the source contributed nothing."""
    rows = [{"source_id": "ga_dca", "name_verbatim": "Trachte", "naics_verbatim": "", "row_hash": "a"},
            {"source_id": "epa_frs", "name_verbatim": "Some Foundry", "naics_verbatim": "331110", "row_hash": "b"}]
    assert candidates([dict(r) for r in rows], {"321992"}) == []                       # neither qualifies
    got = candidates([dict(r) for r in rows], {"321992"}, always={"ga_dca"})
    assert [r["row_hash"] for r in got] == ["a"]                                       # only the asking source
    assert got[0]["_candidate_reason"].startswith("every row of ga_dca")
