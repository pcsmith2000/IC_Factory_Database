from pipeline.contract import COLUMNS, OPTIONAL, ADDED, row_hash, normalise
from pipeline.sources._common import contract_row


def test_optional_fields_are_blank_by_default_and_leave_row_hash_alone():
    src = {"id": "t", "status_basis": "none"}
    a = contract_row(src, 1, name="Plant", source_url="u", source_document="d")
    b = contract_row(src, 1, name="Plant", source_url="u", source_document="d",
                     website="https://plant.example", sq_ft="75,000 sq ft", operating_status="Open")
    assert a["website"] == "" and a["sq_ft"] == "" and a["operating_status"] == ""
    assert b["website"] == "https://plant.example"
    assert b["sq_ft"] == "75000"                  # digits only: "75,000 sq ft" -> 75000
    assert b["operating_status"] == "open"
    assert row_hash(a) == row_hash(b)             # not verbatim: the classifier cache is untouched


def test_normalise_carries_optional_fields_through():
    src = {"id": "t", "status_basis": "none"}
    r = contract_row(src, 1, name="Plant", city="Conyers", state="GA", address="1035 Iris Dr SE",
                     source_url="u", source_document="d", website="w.example", sq_ft="25000")
    out = normalise([r])[0]
    assert out["website"] == "w.example" and out["sq_ft"] == "25000"
    for c in COLUMNS + OPTIONAL + ADDED:
        assert c in out
