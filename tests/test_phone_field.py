"""phone joined the optional contract columns on 2026-09-18. It was already being parsed by four
sources and discarded: mi_lara stripped it to read the name, mo_psc and ga_dca printed it into
notes as prose, and mbma never read the tel: link beside the website it did read."""
from pipeline.contract import OPTIONAL, phone_digits
from pipeline.sources._common import contract_row

SRC = {"id": "x", "status_basis": "none"}


def test_the_same_plant_printed_four_ways_normalises_to_one_number():
    """Rosters print it differently; a field that keeps the punctuation cannot be joined on."""
    assert {phone_digits(x) for x in ["(662) 563-4574", "662-563-4574", "662.563.4574",
                                      "1-662-563-4574", "+1 (662) 563 4574"]} == {"6625634574"}


def test_anything_that_is_not_ten_digits_is_refused_rather_than_truncated():
    """A wrong number reaches a real stranger, so absent beats approximate."""
    for bad in ["662-563-4574 x12", "555-1234", "31021 662-563-4574", "", "see website", "0"]:
        assert phone_digits(bad) == "", bad


def test_it_reaches_the_row_and_leaves_the_verbatim_columns_alone():
    r = contract_row(SRC, 1, name="Acme", source_url="u", source_document="d",
                     phone="(662) 563-4574", website="https://acme.example")
    assert r["phone"] == "6625634574" and r["website"] == "https://acme.example"
    assert "phone" in OPTIONAL


def test_a_row_without_a_phone_still_has_the_column_blank_not_missing():
    r = contract_row(SRC, 1, name="Acme", source_url="u", source_document="d")
    assert r["phone"] == ""


def test_phone_is_not_in_the_row_hash_so_the_classifier_cache_is_untouched():
    from pipeline.contract import row_hash
    a = contract_row(SRC, 1, name="Acme", source_url="u", source_document="d")
    b = contract_row(SRC, 1, name="Acme", source_url="u", source_document="d", phone="662-563-4574")
    assert row_hash(a) == row_hash(b)
