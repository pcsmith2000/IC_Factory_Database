"""ADL's own plant lists carry what nothing else does — and spreadsheet debris that must never
become a number. A guessed square footage and a measured one must not look alike in the warehouse."""
from pipeline.sources import _adl_list

HDR = ("source_id,company_name,address,state,primary_capability,secondary_capability,material_used,"
       "sector_market,factory_size_sf,annual_max_throughput,throughput_unit,vacant_capacity_sf,"
       "utlization_rate,sources,annual_revenue_usd,automation_level,states_serviced,country_based,"
       "phone,email,website,classification_notes,region\n")
SRC = {"id": "adl_july", "url": "x", "status_basis": "none"}


def _rows(tmp_path, *lines):
    p = tmp_path / "adl_july_2026-09-18.csv"; p.write_text(HDR + "".join(lines), encoding="utf-8")
    return _adl_list.read([p], SRC)


def test_a_spreadsheet_error_never_becomes_a_number(tmp_path):
    """15 rows of the real July list carry #DIV/0! in utilisation."""
    r = _rows(tmp_path, 'adl_july,Acme,Troy,TX,Steel Volumetric Modular,,,,,,SF,,#DIV/0!,,,,,,,,,,\n')[0]
    assert r["utilisation_pct"] == ""


def test_not_estimable_and_a_zero_capacity_are_refused_rather_than_coerced(tmp_path):
    """Someone wrote NOT ESTIMABLE to say "we don't know"; a 0 says "this plant makes nothing"."""
    rows = _rows(tmp_path,
        'adl_july,A,Troy,TX,X,,,,23676,NOT ESTIMABLE,SF,,,,,,,,,,,,\n',
        'adl_july,B,Troy,TX,X,,,,190000,0,SF,,,,,,,,,,,,\n')
    assert rows[0]["throughput"] == "" and rows[1]["throughput"] == ""
    assert rows[1]["sq_ft"] == "190000"        # the size is real even when the throughput is not


def test_the_sources_column_is_kept_verbatim_as_value_basis(tmp_path):
    """10 July rows say "CS ChatGPT" and 71 say nothing. The warehouse must be able to tell."""
    rows = _rows(tmp_path,
        'adl_july,A,Lyons,OR,Mass Timber (CLT),,,,170000,2000000,SF,,,CS ChatGPT,,,,,,,,,\n',
        'adl_july,B,Berwick,PA,Wood Volumetric Modular,,,,328000,500000,SF,,,Past Site Visit,,,,,,,,,\n')
    assert rows[0]["value_basis"] == "CS ChatGPT"
    assert rows[1]["value_basis"] == "Past Site Visit"


def test_the_address_column_is_a_city_so_no_row_claims_a_street(tmp_path):
    r = _rows(tmp_path, 'adl_july,Acme,"Willacoochee,",GA,X,,,,169000,,SF,,,,,,,,,,,,\n')[0]
    assert r["address_verbatim"] == "" and r["city_verbatim"] == "Willacoochee"


def test_every_row_is_flagged_for_the_front_ends_toggle(tmp_path):
    r = _rows(tmp_path, 'adl_july,Acme,Troy,TX,X,,,,,,SF,,,,,,,,,,,,\n')[0]
    assert r["adl_validated"] == "1"


def test_a_canadian_row_and_a_nameless_row_are_both_left_out(tmp_path):
    rows = _rows(tmp_path,
        'adl_july,Mercer BC,Okanagan Falls,BC,Mass Timber (CLT),,,,111000,,SF,,,,,,,,,,,,\n',
        'adl_july,,,,,,,,,,SF,,#DIV/0!,,,,,,,,,,\n',
        'adl_july,Real Plant,Troy,TX,X,,,,1000,,SF,,,,,,,,,,,,\n')
    assert [r["name_verbatim"] for r in rows] == ["Real Plant"]


def test_a_name_the_registry_calls_not_a_manufacturer_is_dropped(tmp_path):
    p = tmp_path / "l.csv"
    p.write_text(HDR + 'adl_july,Modutize,Troy,TX,X,,,,1000,,SF,,,,,,,,,,,,\n'
                       'adl_july,Real Plant,Troy,TX,X,,,,1000,,SF,,,,,,,,,,,,\n', encoding="utf-8")
    rows = _adl_list.read([p], SRC, drop_names={"modutize"})
    assert [r["name_verbatim"] for r in rows] == ["Real Plant"]


def test_source_url_is_never_blank_even_when_the_registry_names_none(tmp_path):
    """Run 35406498976 halted at Layer 2 with all 242 rows rejected: source_url is a required
    column and neither registry entry had a url. An internal document still has a place it lives."""
    p = tmp_path / "adl_july_2026-09-18.csv"
    p.write_text(HDR + 'adl_july,Acme,Troy,TX,X,,,,1000,,SF,,,,,,,,,,,,\n', encoding="utf-8")
    r = _adl_list.read([p], {"id": "adl_july", "status_basis": "none"})[0]
    assert r["source_url"] == "blob://ic-sources/adl_july/adl_july_2026-09-18.csv"
