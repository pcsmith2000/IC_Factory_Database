from pipeline.classify import candidates


def _row(name, naics):
    return {"source_id": "epa_frs", "name_verbatim": name, "naics_verbatim": naics}


def test_a_steel_fabricator_on_332312_gets_a_hearing_by_its_metal_not_its_product():
    """BANKER STEEL is four control plants and seven FRS rows, none of which says 'structural'."""
    rows = [_row("BANKER STEEL - ORLANDO", "332312"), _row("VULCRAFT JOIST", "332312"),
            _row("ACME FABRICATORS", "332312"), _row("STEEL CITY BAKERY", "311812")]
    got = candidates(rows, {"321992"})
    assert [r["name_verbatim"] for r in got] == ["BANKER STEEL - ORLANDO", "VULCRAFT JOIST"]
    assert got[0]["_candidate_reason"].endswith("× 3323")


def test_327390_is_admitted_whole_because_precast_plants_rarely_say_precast():
    rows = [_row("CLARK PACIFIC", "327390"), _row("SMITH READY MIX", "327320"),
            _row("CLARK PACIFIC ADELANTO PRECAST PLANT", "327390")]
    got = candidates(rows, {"321992"})
    assert [r["name_verbatim"] for r in got] == ["CLARK PACIFIC", "CLARK PACIFIC ADELANTO PRECAST PLANT"]
    assert got[0]["_candidate_reason"] == "wide code 327390"
