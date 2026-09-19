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


# ---- the additional collection: a different vocabulary, and a collector who had not triaged it

def test_a_label_nobody_listed_is_still_read_when_it_names_making():
    """The additional collection has 25 entity_type values, in phrases. An exact list of them
    fails SILENTLY — a value nobody thought of is dropped without a word, which is how 1,133
    LADBS fabricator licences would have vanished."""
    for et in ("valid_manufacturer_license", "active_prefab_registration", "certified_plant",
               "approved_fabrication_site", "listed_manufacturing_facility",
               "certified_truss_fabricator", "Builder, Dealer/Distributor, Design Professional, "
               "Manufacturing"):
        assert _evidence_csv.is_maker({"entity_type": et}), et


def test_the_refusals_the_first_collection_made_still_hold():
    for et in ("erector", "builder", "supplier", "designer", "accredited_facility",
               "Associate", "provider_profile"):
        assert not _evidence_csv.is_maker({"entity_type": et}), et


def test_a_buildsteel_branch_is_not_a_plant():
    """The collector's own note: "Do not treat branch as manufacturing plant without supporting
    profile evidence" — and 892 of the 1,218 branches are gypsum and building-materials supply
    yards (AD Gypsum Supply, L&W Supply, Gypsum Management)."""
    assert not _evidence_csv.is_maker({"entity_type": "provider_branch_location"})


def test_ac473_is_admitted_on_its_programme_because_a_label_should_not_outrank_a_certificate():
    """IAS lists AC473 facilities with the same word the first collection uses for AC472 post-frame
    BUILDERS. AC473 is "Manufacturers of Cold-Formed Steel Components" — a plant by definition."""
    assert _evidence_csv.is_maker({
        "entity_type": "accredited_facility",
        "certification_program": "Manufacturers of Cold-Formed Steel Components (AC473)"})
    assert not _evidence_csv.is_maker({"entity_type": "accredited_facility",
                                       "certification_program": "IAS AC472 post-frame"})
