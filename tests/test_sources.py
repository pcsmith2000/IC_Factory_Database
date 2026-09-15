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


def _minimal_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with one text line per row — enough for pdfplumber to extract."""
    content = "BT /F1 11 Tf 40 760 Td 14 TL " + " ".join(f"({l.replace('(', '').replace(')', '')}) Tj T*" for l in lines) + " ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out)); out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_pa_xlsx_columns_are_matched_by_name(tmp_path: Path):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Manufacturer Name", "Street Address", "City", "State", "Zip", "Approval Type", "Evaluation Agency"])
    ws.append(["Bladwin Homes", "12 Mill Rd", "Bladwin", "GA", "30511", "Residential", "PFS"])
    ws.append(["", "", "", "", "", "", ""])
    ws.append(["Modtech Canada", "1 Rue X", "Montreal", "QC", "", "Both", "NTA"])
    f = tmp_path / "m.xlsx"; wb.save(f)
    rows = pa_dced.parse([f], SRC("pa_dced"))
    assert [r["name_verbatim"] for r in rows] == ["Bladwin Homes", "Modtech Canada"]
    assert rows[0]["city_verbatim"] == "Bladwin" and rows[0]["state_verbatim"] == "GA"  # typo kept verbatim
    assert rows[0]["row_position"] == "1" and rows[1]["row_position"] == "3"
    assert validate_rows("pa_dced", rows) == []


def test_tx_pdf_entries_group_on_city_state_zip_and_carry_expiry(tmp_path: Path):
    f = tmp_path / "2-Certified_Manufacturers_List.pdf"
    f.write_bytes(_minimal_pdf(["Certified Manufacturers List", "Aura Prefab, LLC", "IHB-12345 Expires 01/31/2027",
                                "5730 Clinton Dr", "Houston, TX 77020", "Legacy Building Solutions", "19500 County Rd 142",
                                "Saint Augusta, MN 56301", "Page 1"]))
    rows = tx_tdlr.parse([f], SRC("tx_tdlr", status_basis="dated_expiry"))
    assert len(rows) == 2
    a = rows[0]
    assert a["name_verbatim"] == "Aura Prefab, LLC" and a["address_verbatim"] == "5730 Clinton Dr"
    assert (a["city_verbatim"], a["state_verbatim"], a["zip_verbatim"]) == ("Houston", "TX", "77020")
    assert a["expiry_date"] == "2027-01-31" and a["status_basis"] == "dated_expiry" and a["source_identifier"] == "IHB-12345"
    assert rows[1]["expiry_date"] == "" and rows[1]["status_basis"] == "on_current_list"
    assert validate_rows("tx_tdlr", rows) == []


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
