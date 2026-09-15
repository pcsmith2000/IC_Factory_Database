"""Parser tests on synthetic fixtures. The live endpoints are exercised by
`python -m pipeline.sources.check <id>` on a machine with network access; these tests pin the
parsing contract so a layout change shows up as a failing test, not a plausible partial pull."""
import csv, io, json, zipfile
from pathlib import Path
import openpyxl
from pipeline.contract import validate_rows
from pipeline.sources import _common, pa_dced, iibc, epa_frs, tx_tdlr, corporate_locations
from pipeline.sources._extract import verbatim_check

SRC = lambda sid, **kw: {"id": sid, "status_basis": "on_current_list", **kw}


def _line(page, top, *pairs):
    """A PDF line as pdf_lines() yields it: (text, x0) pairs at one vertical position."""
    return [{"text": t, "x0": float(x), "x1": float(x) + 6.0 * len(t), "top": float(top), "page": page}
            for t, x in pairs]


# Geometry copied from the real 2026-09-15 TDLR PDF: a whitespace-aligned table whose header
# labels are centred over left-aligned data, with each record wrapping onto a second line.
TX_HEADER = ("Reg", 29), ("#", 47), ("Name", 138), ("Exp", 250), ("Date", 267), ("Physical", 328), \
            ("Address", 364), ("Mailing", 454), ("Address", 487), ("Phone", 572)
TX_LINES = [
    _line(1, 80, *TX_HEADER),
    _line(1, 101, ("IHM-234", 24), ("A", 68), ("&", 75), ("A", 83), ("SHEET", 90), ("METAL", 116),
          ("10/29/2026", 246), ("5122", 302), ("N", 322), ("STATE", 332), ("ROAD", 357), ("39,", 380), ("LA", 394),
          ("PO", 431), ("BOX", 444), ("1848,", 461), ("(219)", 558), ("326-7890", 580)),
    _line(1, 112, ("PORTE,", 302), ("IN", 331), ("46350-1848", 341), ("46352", 431)),
    _line(1, 128, ("IHM-461", 24), ("A1", 68), ("SHEET", 80), ("METAL", 105), ("INC", 132),
          ("4/10/2027", 249), ("9410", 302), ("E.", 322), ("54TH", 331), ("ST,", 352), ("TULSA,", 383), ("OK", 411),
          ("9410", 431), ("E.", 451), ("(918)", 558), ("271-5712", 580)),
    _line(1, 139, ("74112", 302), ("74145", 431)),
    _line(1, 760, ("Page", 100), ("1", 130), ("of", 140), ("13", 150), ("Texas", 300), ("Department", 330)),
    _line(2, 80, *TX_HEADER),
    _line(2, 101, ("IHM-560", 24), ("ABB", 68), ("INC", 85), ("10/25/2026", 246),
          ("6828", 302), ("WILLOWBROOK", 322), ("PARK,", 383), ("305", 431), ("GREGSON", 447),
          ("(860)", 558), ("803-9707", 580)),
    _line(2, 112, ("HOUSTON,", 302), ("OHIO", 344), ("44146", 380), ("27511-6496", 431)),
    _line(2, 760, ("Page", 100), ("2", 130), ("of", 140), ("13", 150), ("Texas", 300), ("Department", 330)),
    _line(3, 80, *TX_HEADER),
    _line(3, 101, ("IHM-364", 24), ("ADVANCED", 68), ("MODULAR", 111), ("11/17/2026", 246),
          ("1168", 302), ("S", 322), ("LEGACY", 328), ("VIEW", 359), ("ST,", 381),
          ("1168", 431), ("S", 451), ("LEGACY", 458), ("(801)", 558), ("571-9841", 580)),
    _line(3, 112, ("SALT", 302), ("LAKE", 322), ("CITY,", 342), ("UT", 362), ("84104", 374), ("LAKE", 431)),
    _line(3, 760, ("Page", 100), ("3", 130), ("of", 140), ("13", 150), ("Texas", 300), ("Department", 330)),
]


def test_tx_columns_are_calibrated_per_file_not_hard_coded(tmp_path: Path):
    cols = _common.detect_columns(TX_LINES, tx_tdlr.LABELS, tmp_path / "f.pdf")
    # each label lands on its DATA column (left-aligned), not on its own centred header
    assert [name for name, _ in cols] == ["Reg", "Exp", "Physical", "Mailing", "Phone"]
    assert [round(x) for _, x in cols] == [24, 246, 302, 431, 558]
    cells = _common.slice_columns(TX_LINES[1], cols)
    assert cells["Reg"].startswith("IHM-234 A & A SHEET METAL")     # Reg + Name share a column by design
    assert cells["Exp"] == "10/29/2026"
    assert cells["Physical"] == "5122 N STATE ROAD 39, LA"
    assert cells["Mailing"] == "PO BOX 1848,"


def test_tx_page_furniture_is_dropped_but_wrapped_address_lines_are_kept():
    kept = _common.drop_repeated_lines(TX_LINES)
    texts = [" ".join(w["text"] for w in ln) for ln in kept]
    assert not any("Page" in t for t in texts), "repeating footer must go"
    assert not any(t.startswith("Reg #") for t in texts), "repeating column header must go"
    assert "74112 74145" in texts, "a wrapped address line repeats in text but not in position — keep it"


def test_tx_records_join_wrapped_lines_and_split_the_address(tmp_path: Path):
    cols = _common.detect_columns(TX_LINES, tx_tdlr.LABELS, tmp_path / "f.pdf")
    recs = tx_tdlr._records(_common.drop_repeated_lines(TX_LINES), cols)
    assert [r["Reg"] for r in recs] == ["IHM-234", "IHM-461", "IHM-560", "IHM-364"]
    assert recs[0]["Name"] == "A & A SHEET METAL"
    assert recs[0]["Physical"] == "5122 N STATE ROAD 39, LA PORTE, IN 46350-1848"
    street, city, state, zip_, country = _common.split_address(recs[0]["Physical"])
    assert (street, city, state, zip_, country) == ("5122 N STATE ROAD 39", "LA PORTE", "IN", "46350-1848", "US")
    # a spelled-out state is normalised; the mailing column never becomes the plant address
    assert _common.split_address(recs[2]["Physical"])[2] == "OH"
    assert "305 GREGSON" in recs[2]["Mailing"]


def test_split_address_leaves_what_it_cannot_parse_whole():
    assert _common.split_address("TOWER 2 FF2 RAKEZ AMENITY CENTRE, RAS AL KHAIMAH, UAE") == (
        "TOWER 2 FF2 RAKEZ AMENITY CENTRE, RAS AL KHAIMAH, UAE", "", "", "", "US")
    assert _common.split_address("3461 FM 934, ITASCA, TX 76055- 4900")[3] == "76055-4900"   # zip wrapped by the PDF
    assert _common.split_address("621 VZ CR 2149, CANTON, TX 75103 -")[3] == "75103"
    assert _common.split_address("101-6420 6A ST SE, CALGARY, ALBERTA T2H2B7") == (
        "101-6420 6A ST SE", "CALGARY", "AB", "T2H2B7", "CA")


def test_iibc_table_with_year_columns(tmp_path: Path):
    html = """<html><body><table>
    <tr><th>Facility</th><th>Address</th><th>2023</th><th>2024</th><th>2025</th></tr>
    <tr><td><a href="https://interstateibc.org/manufacturers/homark-co-inc/">HOMARK CO., INC.</a></td>
        <td>105 West Main St<br>Red Lake Falls, MN 56750</td><td>R</td><td>R</td><td></td></tr>
    <tr><td>Names Only Corp</td><td></td><td></td><td></td><td>R</td></tr>
    </table></body></html>"""
    f = tmp_path / "manufacturers.html"; f.write_text(html)
    rows = iibc.parse([f], SRC("iibc"))
    assert len(rows) == 2
    r = rows[0]
    assert r["name_verbatim"] == "HOMARK CO., INC." and r["address_verbatim"] == "105 West Main St"
    assert (r["city_verbatim"], r["state_verbatim"], r["zip_verbatim"]) == ("Red Lake Falls", "MN", "56750")
    assert r["source_url"].endswith("/homark-co-inc/") and "2023,2024" in r["status_verbatim"]
    assert rows[1]["address_verbatim"] == "" and validate_rows("iibc", rows) == []


def test_epa_zip_is_filtered_by_naics_and_flags_osha(tmp_path: Path):
    def csv_bytes(rows, cols):
        s = io.StringIO(); w = csv.DictWriter(s, fieldnames=cols); w.writeheader(); w.writerows(rows); return s.getvalue().encode()
    fac_cols = ["REGISTRY_ID", "PRIMARY_NAME", "LOCATION_ADDRESS", "CITY_NAME", "STATE_CODE", "POSTAL_CODE", "COUNTRY_NAME", "LATITUDE83", "LONGITUDE83"]
    z = tmp_path / "national_combined.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("NATIONAL_NAICS_FILE.CSV", csv_bytes([
            {"REGISTRY_ID": "1", "NAICS_CODE": "321992"}, {"REGISTRY_ID": "2", "NAICS_CODE": "722511"},
            {"REGISTRY_ID": "3", "NAICS_CODE": "321214"}, {"REGISTRY_ID": "4", "NAICS_CODE": "332311"}], ["REGISTRY_ID", "NAICS_CODE"]))
        zf.writestr("NATIONAL_PROGRAM_FILE.CSV", csv_bytes([{"REGISTRY_ID": "3", "PGM_SYS_ACRNM": "OSHA-OIS"}, {"REGISTRY_ID": "1", "PGM_SYS_ACRNM": "RCRAINFO"}],
                                                          ["REGISTRY_ID", "PGM_SYS_ACRNM"]))
        zf.writestr("NATIONAL_FACILITY_FILE.CSV", csv_bytes([
            {"REGISTRY_ID": "1", "PRIMARY_NAME": "MODULAR HOMES INC", "LOCATION_ADDRESS": "1 A ST", "CITY_NAME": "X", "STATE_CODE": "PA", "POSTAL_CODE": "17815", "COUNTRY_NAME": "USA", "LATITUDE83": "41.0", "LONGITUDE83": "-76.4"},
            {"REGISTRY_ID": "2", "PRIMARY_NAME": "BURGER PLACE", "LOCATION_ADDRESS": "2 B ST", "CITY_NAME": "Y", "STATE_CODE": "PA", "POSTAL_CODE": "", "COUNTRY_NAME": "USA", "LATITUDE83": "", "LONGITUDE83": ""},
            {"REGISTRY_ID": "3", "PRIMARY_NAME": "TRUSS CO", "LOCATION_ADDRESS": "3 C ST", "CITY_NAME": "Z", "STATE_CODE": "OH", "POSTAL_CODE": "44903", "COUNTRY_NAME": "USA", "LATITUDE83": "40.7", "LONGITUDE83": "-82.5"},
            {"REGISTRY_ID": "4", "PRIMARY_NAME": "METAL BLDG", "LOCATION_ADDRESS": "4 D ST", "CITY_NAME": "W", "STATE_CODE": "TX", "POSTAL_CODE": "77020", "COUNTRY_NAME": "USA", "LATITUDE83": "", "LONGITUDE83": ""}], fac_cols))
    src = SRC("epa_frs", status_basis="none", core_naics=["321991", "321992", "332311", "321214"])
    rows = epa_frs.parse([z], src, {"frame": {"naics": ["321991", "321992", "332311", "321214"]}})
    assert [r["source_identifier"] for r in rows] == ["1", "3", "4"]
    assert rows[0]["naics_verbatim"] == "321992" and rows[0]["lat"] == "41.0"
    assert rows[1]["notes"] == "OSHA-OIS" and rows[0]["notes"] == ""
    assert rows[0]["row_position"] == "1" and rows[1]["row_position"] == "3"
    assert validate_rows("epa_frs", rows) == []
    # the archivable slice: only the kept ids, plus the identity of the zip they came from
    with zipfile.ZipFile(tmp_path / "national_combined.filtered.zip") as zs:
        fac = list(csv.DictReader(io.TextIOWrapper(zs.open("NATIONAL_FACILITY_FILE.CSV"), encoding="utf-8")))
        assert [r["REGISTRY_ID"] for r in fac] == ["1", "3", "4"]
        prog = list(csv.DictReader(io.TextIOWrapper(zs.open("NATIONAL_PROGRAM_FILE.CSV"), encoding="utf-8")))
        assert [r["REGISTRY_ID"] for r in prog] == ["3", "1"]
        src = json.loads(zs.read("SOURCE.json"))
        assert src["file"] == "national_combined.zip" and len(src["sha256"]) == 64 and src["rows_kept"] == 3


def test_extraction_verbatim_check_drops_values_not_on_page():
    page = "UFP Site Built — Berlin, NJ\n159 Jackson Rd\nBerlin, New Jersey 08009\nTruss manufacturing facility"
    assert verbatim_check({"name": "UFP Site Built", "address": "159 Jackson Rd", "city": "Berlin"}, page) == []
    assert verbatim_check({"name": "UFP Site Built", "address": "159 Jackson Road", "city": "Berlin"}, page) == ["address"]


def test_split_city_state_zip_never_corrects():
    assert _common.split_city_state_zip("Houston, TX 77020") == ("Houston", "TX", "77020")
    assert _common.split_city_state_zip("Mifflinburg, IN") == ("Mifflinburg", "IN", "")
    assert _common.split_city_state_zip("Somewhere Odd") == ("Somewhere Odd", "", "")
    assert _common.iso_date("13/45/2027") == "" and _common.iso_date("2027-01-31") == "2027-01-31"
