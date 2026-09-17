from pipeline.contract import norm_city, street_key, validate_rows, normalise, COLUMNS

def _row(**kw):
    r = {c: "" for c in COLUMNS}
    r.update(source_id="t", source_url="u", source_document="d", retrieved_date="2026-09-14", row_position="1",
             status_basis="on_current_list")
    r.update(kw); return r

def test_city_variances_collapse():
    assert norm_city("LaGrange") != norm_city("La Grange")  # spacing is a real variance we do NOT invent
    assert norm_city("Saint Paul") == norm_city("ST PAUL")
    assert norm_city("St. Augusta") == norm_city("Saint Augusta")

def test_street_key_survives_city_spelling():
    assert street_key("111 Enterprise Dr.") == street_key("111 ENTERPRISE DRIVE")
    assert street_key("5730 Clinton Drive, Suite 4") == "5730 clinton dr"
    assert street_key("PO Box 12") == ""

def test_required_columns_and_bases():
    ok = _row(name_verbatim="X")
    assert validate_rows("t", [ok]) == []
    bad = _row(retrieved_date="", status_basis="active")
    probs = validate_rows("t", [bad])
    assert any("retrieved_date" in p for p in probs) and any("status_basis" in p for p in probs)

def test_normalise_adds_never_substitutes():
    r = _row(name_verbatim="Bladwin Homes", city_verbatim="Bladwin", state_verbatim="ga")
    n = normalise([r])[0]
    assert n["city_verbatim"] == "Bladwin" and n["state"] == "GA" and n["row_hash"]


def test_street_key_reads_a_written_ordinal_as_a_number():
    """Ess Metron was two facilities in Denver: "1505 W Third Ave" and "1505 W 3rd Ave"."""
    from pipeline.contract import street_key
    assert street_key("1505 W Third Ave") == street_key("1505 W 3rd Ave")
    assert street_key("855 N Fifth St") == street_key("855 N 5th St")
    assert street_key("100 Third St SW") == street_key("100 3rd St SW")


def test_street_key_collapses_a_doubled_street_type():
    """Atkinson Industries was two facilities at one Pittsburg, KS address: one register wrote
    "1801 E 27th St Terrace" and the other "1801 E 27th Terrace"."""
    from pipeline.contract import street_key
    assert street_key("1801 E 27th St Terrace") == street_key("1801 E 27th Terrace")
    assert street_key("4058 Camelot Circle") == street_key("4058 Camelot Cir")
    assert street_key("10642 S Susquehanna Trail") == street_key("10642 S Susquehanna Trl")


def test_a_direction_is_not_a_street_type():
    """"5980 W Sam Houston Pkwy N" ends in a type followed by a direction. Treating the direction
    as a type collapsed it to "5980 w sam houston n" and merged it with a different address."""
    from pipeline.contract import street_key
    assert street_key("5980 W Sam Houston Pkwy N") != street_key("5980 W Sam Houston N")


def test_street_key_still_separates_genuinely_different_streets():
    from pipeline.contract import street_key
    assert street_key("1 Main St") != street_key("1 Main Ave")
    assert street_key("1 Main St") != street_key("2 Main St")
