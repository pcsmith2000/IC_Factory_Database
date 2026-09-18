"""Parser tests on synthetic fixtures. The live endpoints are exercised by
`python -m pipeline.sources.check <id>` on a machine with network access; these tests pin the
parsing contract so a layout change shows up as a failing test, not a plausible partial pull."""
import pytest
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
    # Shape verified against the live page 2026-09-15: Name | Address | City | ST | one column per year
    html = """<html><body><table>
    <tr><th>Name</th><th>Address</th><th>City</th><th>ST</th><th>2023</th><th>2024</th><th>2025</th><th>2026</th></tr>
    <tr><td>A &amp; A SHEET METAL PRODUCTS</td><td>5122 N. STATE RD. 39</td><td>LA PORTE</td><td>IN</td>
        <td>R</td><td>R</td><td>R</td><td>R</td></tr>
    <tr><td>LAPSED PLANT CO</td><td>1 OLD RD</td><td>ERIE</td><td>PA</td><td>R</td><td></td><td></td><td></td></tr>
    <tr><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>
    </table></body></html>"""
    f = tmp_path / "manufacturers.html"; f.write_text(html)
    rows = iibc.parse([f], SRC("iibc"))
    assert len(rows) == 2, "blank rows are skipped, not emitted"
    r = rows[0]
    assert r["name_verbatim"] == "A & A SHEET METAL PRODUCTS" and r["address_verbatim"] == "5122 N. STATE RD. 39"
    assert (r["city_verbatim"], r["state_verbatim"]) == ("LA PORTE", "IN")
    assert r["status_verbatim"] == "registered 2023,2024,2025,2026" and r["status_basis"] == "certified_as_of_date"
    assert rows[1]["status_verbatim"] == "registered 2023"      # a lapsed plant keeps its last year
    assert validate_rows("iibc", rows) == []


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


def test_a_location_label_is_qualified_with_the_operating_company():
    """84 Lumber's page names the SITE, never the operator.

    "Kings Mountain Truss Plant" reached the warehouse with nothing tying it to 84 Lumber — not a
    reader, not the control list, not a cross-source join. The company goes in FRONT of the label
    rather than replacing it, because the label is the only thing separating one 84 Lumber plant
    from another and this database is plant-level.
    """
    from pipeline.sources.corporate_locations import _qualify
    assert _qualify("84 Lumber", "Kings Mountain Truss Plant") == "84 Lumber — Kings Mountain Truss Plant"
    assert _qualify("The Truss Company", "Sumner") == "The Truss Company — Sumner"
    # already carries the company: left exactly as the page wrote it
    assert _qualify("Stark Truss", "Stark Truss - Summerville") == "Stark Truss - Summerville"
    assert _qualify("UFP Site Built", "UFP Site Built Grand Rapids") == "UFP Site Built Grand Rapids"
    # no company on record is not a reason to mangle the label
    assert _qualify("", "Orphan Plant") == "Orphan Plant"


def test_qualified_labels_stay_distinct_per_plant():
    """Plant-level is the point: qualifying must not collapse two sites into one name."""
    from pipeline.sources.corporate_locations import _qualify
    a = _qualify("84 Lumber", "Kings Mountain Truss Plant")
    b = _qualify("84 Lumber", "Coal Center Truss Plant")
    assert a != b and a.startswith("84 Lumber") and b.startswith("84 Lumber")


def test_bldr_keeps_only_manufacturing_branches():
    """MF is a plant; YD is a lumber yard and MW is doors and mouldings."""
    from pipeline.sources import bldr_locations as B
    html = ('<a href="/location/acworth-ga-truss/ACWOGAMF">x</a>'
            '<a href="/location/abilene-tx-lumber-yard/ABILTXYD">x</a>'
            '<a href="/location/abilene-tx-millwork/ABILTXMW">x</a>'
            '<a href="/location/albemarle-nc-truss/ALBENCMF">x</a>')
    assert [c for _s, c in B._mf_links(html)] == ["ACWOGAMF", "ALBENCMF"]
    assert B._kind_counts(html) == {"MF": 2, "YD": 1, "MW": 1}


def test_bldr_never_takes_the_corporate_footer_address(tmp_path):
    """Every bldr.com page footers the Irving, TX head office.

    Taking the first street-shaped string in the HTML gave the Albuquerque plant an address in
    Texas. Only the location block's own placeLink counts, and only where its state agrees with
    the title's.
    """
    from pipeline.sources import bldr_locations as B
    src = {"id": "bldr_locations", "url": B.INDEX, "status_basis": "on_current_list"}
    (tmp_path / "all-locations.html").write_text('<a href="/location/albuquerque-nm-truss/ALBQNMMF">x</a>')
    (tmp_path / "ALBQNMMF.html").write_text(
        "<title>Albuquerque NM Truss | Builders FirstSource</title>"
        '<a href="https://www.google.com/maps/place/119 Llano Del Sur South East,Albuquerque,NM,87105/"'
        ' class="placeLink">here</a>'
        "<footer>Builders FirstSource<br />6031 Connection Dr<br />Irving, TX 75039</footer>")
    rows = B.parse(sorted(tmp_path.glob("*.html")), src)
    assert len(rows) == 1
    assert rows[0]["address_verbatim"] == "119 Llano Del Sur South East"
    assert (rows[0]["city_verbatim"], rows[0]["state_verbatim"]) == ("Albuquerque", "NM")
    assert "Builders FirstSource" in rows[0]["name_verbatim"]
    assert rows[0]["source_identifier"] == "ALBQNMMF"


def test_bldr_drops_a_street_whose_state_contradicts_the_title():
    """A page rendering another branch's block loses its street; it does not inherit one."""
    import tempfile, pathlib
    from pipeline.sources import bldr_locations as B
    d = pathlib.Path(tempfile.mkdtemp())
    src = {"id": "bldr_locations", "url": B.INDEX, "status_basis": "on_current_list"}
    (d / "all-locations.html").write_text('<a href="/location/x/AAAABBMF">x</a>')
    (d / "AAAABBMF.html").write_text(
        "<title>Somewhere NM Truss | Builders FirstSource</title>"
        '<a href="https://www.google.com/maps/place/1 Wrong St,Elsewhere,TX,75039/" class="placeLink">x</a>')
    rows = B.parse(sorted(d.glob("*.html")), src)
    assert rows[0]["address_verbatim"] == ""


def test_sipa_takes_the_street_only_from_the_members_own_paragraph(tmp_path):
    """These profile sidebars also render projects and sponsors.

    A street lifted from the wrong block is worse than no street, so the address paragraph is
    believed only where it is headed by the member's own name.
    """
    from pipeline.sources import sipa
    src = {"id": "sipa", "url": sipa.INDEX, "status_basis": "on_current_list"}
    (tmp_path / "manufacturing.html").write_text(
        '<a href="/members/acme-panel-company" class="font-weight-bold">ACME Panel Company</a>'
        "<strong>Manufacturing</strong><small>Radford, VA</small><footer>x</footer>")
    (tmp_path / "acme-panel-company.html").write_text(
        "<p><strong>Zero-Energy SIP Demonstration House</strong><br />9 Someone Else Rd<br />"
        "Vienna, VA 22180<br />United States</p>"
        "<p><strong>ACME Panel Company</strong><br />1905 W Main St<br />"
        "Radford, VA 24141<br />United States</p>")
    rows = sipa.parse(sorted(tmp_path.glob("*.html")), src)
    assert len(rows) == 1
    assert rows[0]["address_verbatim"] == "1905 W Main St"
    assert (rows[0]["city_verbatim"], rows[0]["state_verbatim"]) == ("Radford", "VA")


def test_sipa_keeps_a_member_whose_profile_has_no_readable_address(tmp_path):
    """Name, city and state already locate a plant here; dropping the row would have the database
    claim a SIPA manufacturer does not exist."""
    from pipeline.sources import sipa
    src = {"id": "sipa", "url": sipa.INDEX, "status_basis": "on_current_list"}
    (tmp_path / "manufacturing.html").write_text(
        '<a href="/members/foard-panel-inc" class="font-weight-bold">Foard Panel, Inc.</a>'
        "<strong>Manufacturing</strong><small>West Chesterfield, NH</small><footer>x</footer>")
    (tmp_path / "foard-panel-inc.html").write_text("<p>no address paragraph here</p>")
    rows = sipa.parse(sorted(tmp_path.glob("*.html")), src)
    assert len(rows) == 1 and rows[0]["address_verbatim"] == ""
    assert rows[0]["city_verbatim"] == "West Chesterfield"


def test_mass_timber_shares_one_state_across_several_towns():
    """"Drain, Portland & Swisshome, OR" is three Oregon plants, not one town called all that."""
    from pipeline.sources.woodworks_mass_timber import _places
    assert _places(["Drain, Portland & Swisshome, OR;", "Piedmont, SC"]) == [
        ("Drain", "OR"), ("Portland", "OR"), ("Swisshome", "OR"), ("Piedmont", "SC")]


def test_mass_timber_skips_canada_without_coercing_it():
    from pipeline.sources.woodworks_mass_timber import _places
    assert _places(["Conway, AR; Okanagan Falls, BC;", "Spokane, WA"]) == [("Conway", "AR"), ("Spokane", "WA")]
    assert _places(["Boissevian, MB; Edmonton, AB;", "Sturgeon County, AB"]) == []


def test_a_stray_pdf_artifact_line_cannot_swallow_the_line_above_it():
    """Joining the location lines appended the PDF's own filename to the last entry and the
    segment stopped matching — Western Forest Products' Washougal plant vanished silently."""
    from pipeline.sources.woodworks_mass_timber import _places
    assert _places(["Vancouver & Washougal, WA",
                    "FFRRAA--994499__MMAANNUUFFAACCTTUURREERR__LLOOCCAATTIIOONNSS__MMAAPP__JJaann22002266..iinndddd"]) \
        == [("Vancouver", "WA"), ("Washougal", "WA")]


def test_mass_timber_entries_ignore_the_pages_own_prose():
    """A line is a company name only when the next line is the parenthesised product list."""
    from pipeline.sources.woodworks_mass_timber import _entries
    e = _entries(["As a non-profit, WoodWorks", "They represent the mass timber",
                  "Mercer", "(CLT, GLT, Glulam, fabricator)", "Conway, AR;", "Spokane, WA",
                  "Quality Buildings", "(Fabricator)", "Lancaster, PA"])
    assert [x["name"] for x in e] == ["Mercer", "Quality Buildings"]
    assert e[0]["locations"] == ["Conway, AR;", "Spokane, WA"]


def test_indiana_keeps_a_us_plant_whose_zip_is_dirty_or_missing():
    """Requiring a clean 5-digit ZIP threw away six real plants.

    "Marco Island, FL" carries none at all, "Hitchcock, TX 775636" has six digits and
    "Aubrey, TX 762278030" has nine unhyphenated. Those are typos in a state register, not evidence
    that the row is not a plant. The ZIP is kept exactly as filed.
    """
    from pipeline.sources.in_dhs import _listings
    page = ('<div class="listing"><h3><a href="?method=view&manufacturerNameId=1">Alt Construction</a></h3>'
            '<p class="listingInfo">992 Winterbery Dr<br />Marco Island, FL<br /></p>'
            '<div class="listing"><h3><a href="?method=view&manufacturerNameId=2">Parkline</a></h3>'
            '<p class="listingInfo">5235 Delaney Rd<br />Hitchcock, TX 775636<br /></p>')
    got = {r["name"]: (r["city"], r["state"], r["zip"], r["foreign"]) for r in _listings(page)}
    assert got["Alt Construction"] == ("Marco Island", "FL", "", False)
    assert got["Parkline"] == ("Hitchcock", "TX", "775636", False)


def test_indiana_tells_a_us_city_line_from_a_canadian_one_by_the_state_code():
    """"Hamilton, ON" fails the US-state test and "Marco Island, FL" passes it — no second pattern
    for postal codes to keep in sync."""
    from pipeline.sources.in_dhs import _listings
    page = ('<div class="listing"><h3><a href="?method=view&manufacturerNameId=3">Philip Doyle</a></h3>'
            '<p class="listingInfo">75 Covington St<br />Hamilton, ON<br /></p>')
    r = _listings(page)[0]
    assert r["foreign"] is True and r["city"] == "" and r["state"] == ""


def test_indiana_finds_the_city_line_past_a_po_box():
    """"3549 Highway 16 North / P O Box 428 / Denver, NC 28037" — the box must not become the town."""
    from pipeline.sources.in_dhs import _listings
    page = ('<div class="listing"><h3><a href="?method=view&manufacturerNameId=4">Boegh</a></h3>'
            '<p class="listingInfo">3549 Highway 16 North<br />P O Box 428<br />Denver, NC 28037<br /></p>')
    r = _listings(page)[0]
    assert (r["street"], r["city"], r["state"]) == ("3549 Highway 16 North", "Denver", "NC")
    assert r["extra"] == "P O Box 428"


def test_superior_walls_takes_the_licensee_address_not_the_corporate_one():
    """Every page carries both. New Holland, PA 17557 is Superior Walls of America, not a plant —
    the same trap that put bldr.com's Albuquerque plant in Irving, Texas."""
    from pipeline.sources.superior_walls import _licensee
    page = ("<p>Contact Information<br>Superior Walls by Advanced Concrete<br>570-837-3955<br>"
            "55 Advanced Lane<br>Middleburg, PA 17842</p>"
            "<p>CORPORATE OFFICES<br>Superior Walls<br>937 East Earl Road<br>"
            "New Holland, PA 17557</p>")
    lic = _licensee(page)
    assert lic == {"name": "Superior Walls by Advanced Concrete", "street": "55 Advanced Lane",
                   "city": "Middleburg", "state": "PA", "zip": "17842"}


def test_superior_walls_rejects_a_page_that_is_only_the_corporate_office():
    """A products or news page has no licensee on it, and must not yield the head office as one."""
    from pipeline.sources.superior_walls import _licensee
    assert _licensee("<p>CORPORATE OFFICES<br>Superior Walls<br>937 East Earl Road<br>"
                     "New Holland, PA 17557</p>") is None
    assert _licensee("<p>Contact Information<br>Superior Walls<br>717-351-9255<br>"
                     "937 East Earl Road<br>New Holland, PA 17557</p>") is None


def test_superior_walls_skips_a_non_us_licensee():
    """The network includes Alberta and the Bahamas; this is a US plant database."""
    from pipeline.sources.superior_walls import _licensee
    assert _licensee("<p>Contact Information<br>Superior Walls of Alberta<br>780-555-1212<br>"
                     "12 Industrial Way<br>Leduc, AB 99999</p>") is None


def test_superior_walls_needs_the_line_breaks_to_split_street_from_city():
    """Flattening the page loses the <br> between them and "55 Advanced Lane Middleburg" has no
    delimiter left — the first attempt read the street as "55 Advanced" and the town as "Lane
    Middleburg"."""
    from pipeline.sources.superior_walls import _lines
    got = _lines("<p>Contact Information<br>55 Advanced Lane<br>Middleburg, PA 17842</p>")
    assert got == ["Contact Information", "55 Advanced Lane", "Middleburg, PA 17842"]


def test_superior_walls_counts_a_plant_once_across_two_slugs():
    """Warrior Precast is both /superior-walls-warrior-precast/ and /superior-walls-east-tennessee/.
    The street and ZIP are the plant; the slug is only the page."""
    import tempfile, pathlib
    from pipeline.sources import superior_walls as SW
    d = pathlib.Path(tempfile.mkdtemp())
    block = ("<p>Contact Information<br>Superior Walls by Warrior Precast<br>931-555-0100<br>"
             "10144 Sparta Hwy.<br>Rock Island, TN 38581</p>")
    (d / "superior-walls-warrior-precast.html").write_text(block)
    (d / "superior-walls-east-tennessee.html").write_text(block)
    rows = SW.parse(sorted(d.glob("*.html")), {"id": "superior_walls", "url": SW.BASE,
                                               "status_basis": "on_current_list"})
    assert len(rows) == 1
    assert "1 licensee plants from 2 pages; 1 pages were a second slug" in rows[0]["notes"]


def test_a_bare_town_label_is_not_written_into_the_company_name():
    """The Truss Company lists its plants as "Sumner, WA" and "Eugene, OR".

    Qualifying those produced "The Truss Company — Sumner, WA", and norm_name strips "The" and
    "Company": the control row normalised to "truss" while the facility normalised to
    "trusssumnerwa". All eight plants were extracted correctly, with street addresses, and none of
    them matched anything. The town already lives in the city and state columns.
    """
    from pipeline.sources.corporate_locations import _qualify
    assert _qualify("The Truss Company", "Sumner, WA") == "The Truss Company"
    assert _qualify("The Truss Company", "Medford, OR") == "The Truss Company"
    assert _qualify("Banker Steel", "Lynchburg, VA") == "Banker Steel"
    # a label that names a SITE is still qualified — that is what 84 Lumber needs
    assert _qualify("84 Lumber", "Kings Mountain Truss Plant") == "84 Lumber — Kings Mountain Truss Plant"
    assert _qualify("UFP Site Built", "Shawnlee Construction") == "UFP Site Built — Shawnlee Construction"


def test_a_transcribed_csv_wins_for_its_own_company_only(tmp_path):
    """Six carried-forward CSVs silently suppressed every HTML page in the folder.

    parse() returned as soon as any CSV existed, so Banker Steel and True House — which have no
    transcription and exist only as pages — produced nothing. The docstring has always said "one
    file per company ... the others keep their rows".
    """
    import csv as _csv
    from pipeline.sources import corporate_locations as CL
    (tmp_path / "stark-truss.csv").write_text("")
    with open(tmp_path / "stark-truss.csv", "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=CL.PRE_EXTRACTED_COLUMNS); w.writeheader()
        w.writerow({"company": "Stark Truss", "name": "Stark Truss - Summerville", "address": "1 A St",
                    "city": "Summerville", "state": "SC", "zip": "29483", "kind": "plant",
                    "evidence": "", "source_url": "https://www.starktruss.com/locations/"})
    (tmp_path / "banker-steel").mkdir()
    (tmp_path / "banker-steel" / "index.html").write_text("<p>" + "x " * 300 + "</p>")
    paths = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    src = {"id": "corporate_locations", "status_basis": "on_current_list",
           "pages": [{"company": "Stark Truss", "url": "u"}, {"company": "Banker Steel", "url": "u"}]}

    seen = {}
    def fake_extract(text, company, page_url, cfg, prompt_path, archive_to):
        seen["company"] = company
        return {"locations": [], "dropped": [], "model": "test", "prompt_hash": "0"}
    CL.extract_locations, real = fake_extract, CL.extract_locations
    try:
        rows = CL.parse(paths, src, {})
    finally:
        CL.extract_locations = real
    # the transcribed company came from its CSV, and was NOT sent to the model
    assert [r["name_verbatim"] for r in rows] == ["Stark Truss - Summerville"]
    # the company WITHOUT a transcription still reached extraction
    assert seen.get("company") == "Banker Steel"


# The two tests below assert on the agent sandbox's browser stack — the proxy CA at
# /root/.ccr/agent-proxy-ca.crt and the Chromium preinstalled under PLAYWRIGHT_BROWSERS_PATH.
# Both are real and worth pinning where they exist; neither exists on a GitHub runner, where they
# failed as PermissionError and "no Chromium found" and had CI red on every branch that carried
# them. Skipping on absence keeps the assertion where it means something instead of deleting it.
def _ca_readable() -> bool:
    from pipeline.sources._browser import CA_FILES
    import pathlib as _p
    for f in CA_FILES:
        try:
            if _p.Path(f).read_bytes():
                return True
        except OSError:
            continue
    return False


def _chromium_present() -> bool:
    from pipeline.sources import _browser
    try:
        return bool(_browser._chromium_path())
    except OSError:
        return False


@pytest.mark.skipif(not _ca_readable(),
                    reason="no readable agent-proxy CA here; this pins sandbox browser TLS")
def test_the_browser_pins_the_proxy_ca_by_spki_rather_than_disabling_tls():
    """Chromium reads the NSS store, not the CA env vars, and this image has no certutil.

    The CA is pinned with --ignore-certificate-errors-spki-list, which whitelists specific public
    keys; it is NOT --ignore-certificate-errors, which would switch verification off. The pins are
    read out of the CA file at launch, so a rotated CA is picked up instead of a stale fingerprint.
    """
    import base64
    from pipeline.sources import _browser
    pins = _browser._spki_pins()
    assert pins, "no SPKI pins read from the agent-proxy CA — a browser source cannot reach TLS"
    for p in pins:
        assert len(base64.b64decode(p)) == 32      # SHA-256 of the SubjectPublicKeyInfo


def test_the_browser_never_asks_playwright_to_download_a_second_chromium():
    """The image ships Chromium under PLAYWRIGHT_BROWSERS_PATH and the docs say not to fetch one.

    The source rule holds anywhere, so it is asserted anywhere; finding the binary only makes
    sense where the image that ships it is.
    """
    from pipeline.sources import _browser
    src = (__import__("pathlib").Path(_browser.__file__)).read_text()
    assert "playwright install" not in src.replace("do NOT run `playwright install`", "")


@pytest.mark.skipif(not _chromium_present(),
                    reason="no preinstalled Chromium here; this pins the sandbox image's browser")
def test_the_preinstalled_chromium_is_found_where_the_image_puts_it():
    from pipeline.sources import _browser
    assert _browser._chromium_path(), "no Chromium found under PLAYWRIGHT_BROWSERS_PATH"


def test_pci_keeps_building_precast_and_skips_infrastructure():
    """The certification covers rail ties and box culverts as readily as wall panels."""
    from pipeline.sources.pci_certified import _is_building_precast
    assert _is_building_precast("Architectural Precast, Double Tees, Structural Wall Panels")
    assert _is_building_precast("Bleachers, Beams, Columns")
    assert not _is_building_precast("Rail Road Ties")
    assert not _is_building_precast("Box Culverts, Pipe, Piles")


def test_pci_street_starts_at_the_house_number():
    """Cells run name-then-address and many names carry their own comma, so the segment before the
    city is "Inc. 34956 Co Rd 126" — stripping the name off the front does not help."""
    from pipeline.sources.pci_certified import _rows
    page = ('<tr class="rgRow"><td>Basin Precast, Inc. 34956 Co Rd 126, Sidney, MT 59270 '
            'United States Certification Category: C3 Products Produced: Double Tees</td>'
            '<td>Basin Precast, Inc.</td></tr>')
    r = _rows(page)[0]
    assert r["street"] == "34956 Co Rd 126"
    assert (r["city"], r["state"], r["zip"]) == ("Sidney", "MT", "59270")
    assert r["products"] == "Double Tees"


def test_pci_sweeps_us_states_by_label_not_by_code():
    """"WA" is Washington AND Western Australia in the same dropdown."""
    from pipeline.sources.pci_certified import _options
    page = ('<select id="x_Input3_DropDown1">'
            '<option value="WA:::78">Washington</option>'
            '<option value="WA:::80">Western Australia</option>'
            '<option value="AB:::2">Alberta</option>'
            '<option value="MT:::26">Montana</option></select>')
    assert _options(page) == [("WA:::78", "WA"), ("MT:::26", "MT")]


def test_pci_refuses_a_sweep_that_is_empty_for_most_states(tmp_path):
    """The 2026-09-17 sweep archived 46 pages, 42 of them the empty form, and would have published
    "10 plants across 46 states" from the page's own default region. A roster that is empty for
    most of the country is a broken query, not a thin industry."""
    import pytest
    from pipeline.sources import pci_certified as P
    from pipeline.sources._common import LayoutChanged
    row = ('<tr class="rgRow"><td>Wells 2145 E Crown Prince Blvd, Brighton, CO 80603 '
           'Certification Category: AC Products Produced: Double Tees</td><td>Wells</td></tr>')
    (tmp_path / "state-CO.html").write_text(row)
    for st in ("TX", "PA", "CA"):
        (tmp_path / f"state-{st}.html").write_text("<table></table>")
    with pytest.raises(LayoutChanged, match="only 1 of 4 state pages"):
        P.parse(sorted(tmp_path.glob("*.html")), {"id": "pci_certified", "url": P.URL,
                                                  "status_basis": "certified"})
