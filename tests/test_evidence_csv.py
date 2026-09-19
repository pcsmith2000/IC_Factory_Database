"""The 2026-09-18 evidence collection ships its own README, and these rules are quoted from it
rather than guessed: "Supplier does not mean factory; builder does not mean manufacturer",
"AISC covers the US directory, including erectors and infrastructure fabricators"."""
from pipeline.sources import _evidence_csv

HDR = ("record_id,source,source_record_id,company_name,entity_type,street_address,city,"
       "state_province,postal_code,country,latitude,longitude,website,phone,product_types,"
       "certification_program,certification_categories,source_status,certificate_expires,"
       "inspection_date,naics_code,ic_scope,inclusion_basis,source_url,source_detail_url,"
       "retrieved_at,data_quality_notes,source_location_raw\n")
SRC = {"id": "ic_directories", "url": "x", "status_basis": "on_current_list"}


def _rows(tmp_path, *lines):
    p = tmp_path / "ic_directory_evidence_all_2026-09-18.csv"
    p.write_text(HDR + "".join(lines), encoding="utf-8")
    return _evidence_csv.read([p], SRC)


def _row(name, entity, scope="building_components", country="United States", st="TX"):
    return (f"r1,AISC,x,{name},{entity},1 Mill Rd,Troy,{st},75001,{country},32.1,-96.1,"
            f"http://x.example,214-555-1212,Trusses,AISC,Building Fabricator,Current,,,,"
            f"{scope},AISC certification category,http://d,http://d2,2026-09-18,,\n")


def test_an_erector_is_not_published_as_a_factory(tmp_path):
    """537 of AISC's US rows are erectors. They erect steel; they do not make it."""
    rows = _rows(tmp_path, _row("Acme Fab", "fabricator"), _row("Bolt Co", "erector"),
                 _row("Sells Ltd", "supplier"), _row("Draws Inc", "designer"),
                 _row("Builds LLC", "builder"))
    assert [r["name_verbatim"] for r in rows] == ["Acme Fab"]


def test_the_vendor_ecosystem_and_infrastructure_only_scopes_are_left_out(tmp_path):
    rows = _rows(tmp_path, _row("Bridge Shop", "fabricator", scope="infrastructure_only"),
                 _row("Vendor", "fabricator", scope="vendor_ecosystem"),
                 _row("Real Plant", "fabricator", scope="building_components"))
    assert [r["name_verbatim"] for r in rows] == ["Real Plant"]


def test_a_canadian_row_is_not_a_us_plant(tmp_path):
    rows = _rows(tmp_path, _row("Savona", "mill", country="Canada", st="BC"),
                 _row("Real Plant", "mill"))
    assert [r["name_verbatim"] for r in rows] == ["Real Plant"]


def test_the_evidence_for_the_judgement_survives_into_notes(tmp_path):
    """A row that looks wrong has to be arguable, so the triage inputs ride along."""
    n = _rows(tmp_path, _row("Acme Fab", "fabricator"))[0]["notes"]
    for expected in ("entity_type: fabricator", "ic_scope: building_components",
                     "product_types: Trusses", "certification_program: AISC",
                     "source_status: Current"):
        assert expected in n, expected


def test_the_contact_and_location_columns_all_reach_the_row(tmp_path):
    r = _rows(tmp_path, _row("Acme Fab", "fabricator"))[0]
    assert r["address_verbatim"] == "1 Mill Rd" and r["zip_verbatim"] == "75001"
    assert r["phone"] == "2145551212" and r["website"] == "http://x.example"
    assert (r["lat"], r["lon"]) == ("32.1", "-96.1")


def test_an_osha_inspection_record_counts_as_a_maker(tmp_path):
    """An inspection is a federal officer standing in the building — the strongest address there is."""
    rows = _rows(tmp_path, _row("Plant Under Inspection", "inspection_record",
                                scope="industry_lead_requires_verification"))
    assert len(rows) == 1
