"""sbca_cm is the truss segment's only roster: no state licences a truss plant, none files a HUD
registration, and EPA FRS sees one only if it happens to hold an environmental permit."""
from pathlib import Path
from pipeline.sources import sbca_cm

HEADER = ("source_id,name,legal_entity,address,city,state,zip,status,expires,registration_id,"
          "product_type,evidence,source_url,retrieved_date,state_raw,country,lat,lon,"
          "geocode_accuracy,phone,website\n")
CAVEAT = ("SBCA Component Manufacturer member plotted on the BatchGeo map. Coordinates are "
          "geocodes of the member mailing address - NOT a verified plant location")


def _csv(tmp_path, *rows):
    p = tmp_path / "sbca_cm_members_2026-09-18.csv"
    p.write_text(HEADER + "".join(rows), encoding="utf-8")
    return [p]


def test_a_canadian_member_is_not_published_as_a_us_plant(tmp_path):
    """63 of the 932 rows are Canadian provinces; PE and AB are not US states."""
    paths = _csv(tmp_path,
        f'sbca_cm,Apex Truss,,1 Mill Rd,Warsaw,VA,22572,,,,,"{CAVEAT}",https://x,2026-09-18,VA,US,37.9,-76.7,ROOFTOP,804-333-4444,apextruss.com\n',
        f'sbca_cm,Western Truss,,39709 Western Rd,Elmsdale,PE,C0B 1K0,,,,,"{CAVEAT}",https://x,2026-09-18,PE,CA,46.8,-64.1,ROOFTOP,902-853-3539,\n')
    rows = sbca_cm.parse(paths, {"id": "sbca_cm", "url": "x", "status_basis": "on_current_list"})
    assert [r["name_verbatim"] for r in rows] == ["Apex Truss"]


def test_the_phone_and_website_reach_the_contract_columns(tmp_path):
    paths = _csv(tmp_path,
        f'sbca_cm,Apex Truss,,1 Mill Rd,Warsaw,VA,22572,,,,,"{CAVEAT}",https://x,2026-09-18,VA,US,37.9,-76.7,ROOFTOP,(804) 333-4444,apextruss.com\n')
    r = sbca_cm.parse(paths, {"id": "sbca_cm", "url": "x", "status_basis": "on_current_list"})[0]
    assert r["phone"] == "8043334444" and r["website"] == "apextruss.com"
    assert (r["lat"], r["lon"]) == ("37.9", "-76.7")


def test_the_transcribers_caveat_survives_into_notes_verbatim(tmp_path):
    """Every coordinate is a mailing-address geocode claiming ROOFTOP. The row must say so."""
    paths = _csv(tmp_path,
        f'sbca_cm,Apex Truss,,1 Mill Rd,Warsaw,VA,22572,,,,,"{CAVEAT}",https://x,2026-09-18,VA,US,37.9,-76.7,ROOFTOP,804-333-4444,\n')
    r = sbca_cm.parse(paths, {"id": "sbca_cm", "url": "x", "status_basis": "on_current_list"})[0]
    assert "NOT a verified plant location" in r["notes"]


def test_it_refuses_to_fetch_rather_than_pretending_the_site_answers(tmp_path):
    """sbcindustry.com returns no HTTP status at all; the roster is a hand transcription."""
    import pytest
    with pytest.raises(NotImplementedError):
        sbca_cm.pull({"id": "sbca_cm"}, {}, tmp_path)
