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
