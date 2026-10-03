"""Job-site candidates: inspection records at construction sites, proposed for a person's ruling.

The rows are the warehouse's own (2026-10-03), with the building pass's numbers."""
from pipeline import jobsite as J
from pipeline import warehouse


def g(fid, name, address, city, state="WA", inspection_only=0, max_sqft=None, point_sqft=None, review=None):
    return {"facility_id": fid, "name": name, "address": address, "city": city, "state": state,
            "inspection_only": inspection_only, "max_building_sqft": max_sqft,
            "point_building_sqft": point_sqft, "building_review": review}


PLANTS = [
    g("IC-90459", "The Truss Company", "2802 142ND AVE E", "SUMNER", max_sqft=357321, point_sqft=89201, review="contains_point"),
    g("IC-90888", "The Truss Company - Pasco", "355 N COMMERCIAL AVE", "PASCO", max_sqft=100138, point_sqft=100138, review="contains_point"),
    g("IC-95253", "The Truss Company", "202 ROBERT THOMPSON RD", "CENTRALIA", max_sqft=148644, point_sqft=79812, review="contains_point"),
    g("IC-90744", "LOUWS TRUSS INC", "5426 BARRETT RD STE A-1", "FERNDALE", max_sqft=18099, point_sqft=4950, review="small_building"),
    g("IC-95842", "Louws Truss, Inc. - Cashmere", "5485 MILL RD", "CASHMERE", max_sqft=19241, point_sqft=19241, review="contains_point"),
    g("IC-10372", "PACIFIC WOODTECH CORP.", "11500 Reading Rd", "RED BLUFF", state="CA"),
]
JOBSITES = [
    g("IC-90838", "WA317940369 - TRUSS COMPANY & BUILDING SUPPLY INC THE", "HARBOR HILL S5LOT 1", "GIG HARBOR",
      inspection_only=1, max_sqft=47085, review="none"),
    g("IC-95981", "WA317976402 - THE TRUSS COMPANY & BUILDING SUPPLY LLC", "4497 WANDERING WAY", "PORT ORCHARD",
      inspection_only=1, review="none"),
    g("IC-95868", "WA317969123 - THE TRUSS COMPANY & BUILDING SUPPLY LLC", "20754 E. VALLEY VISTA DR.", "LIBERTY LAKE",
      inspection_only=1, max_sqft=2783, point_sqft=2746, review="small_building"),
    g("IC-90872", "WA317944963 - LOUWS TRUSS INC", "2920 CODY LANE", "BELLINGHAM",
      inspection_only=1, max_sqft=37685, point_sqft=3409, review="small_building"),
    # Trus Way has no plant record in golden, so only the buildings speak.
    g("IC-90512", "WA317935888 - TRUS WAY OF TRI CITIES INC", "4622 TAMARISK DR.", "PASCO",
      inspection_only=1, max_sqft=15537, point_sqft=3412, review="small_building"),
    g("IC-90564", "WA317943744 - TRUS WAY OF TRI CITIES INC", "6614 KNOCKING POINT ROAD", "PASCO",
      inspection_only=1, max_sqft=6276, point_sqft=4465, review="small_building"),
    g("IC-90862", "WA317939653 - PHOENIX TRUSS CORPORATION THE", "1725 EAST PALOS VERDES", "OTHELLO",
      inspection_only=1, max_sqft=3526, point_sqft=2055, review="small_building"),
]
# The counter-example: a real plant, inspected at its own door. The geocode fell on a 1,156 sqft
# outbuilding, but the 286,429 sqft plant stands beside it and the company has no other WA plant.
PACIFIC_WOODTECH = g("IC-90264", "WA317946391 - PACIFIC WOODTECH CORPORATION", "1850 PARK LANE", "BURLINGTON",
                     inspection_only=1, max_sqft=286429, point_sqft=1156, review="small_building")


def by_id(rows):
    return {c["facility_id"]: c for c in J.candidates(rows)}


def test_the_prefix_is_read_in_all_its_shapes_and_nothing_else():
    for name in ("WA317946391 - PACIFIC WOODTECH CORPORATION", "317711188 - THE TRUSS COMPANY",
                 "70260 - QUALITY WOODTRUSS INCORPORATED", "137651 - BLUE RIDGE BUILDING COMPONENTS, INC.",
                 "CPX2024XEG419X0024 - MYLAND COMPANY, INC."):
        assert J.INSPECTION_PREFIX.match(name), name
    for name in ("1012006 CLAYTON ADDISON", "942 Clayton TRU Lynn", "998-Belton 2", "360 MODULAR",
                 "84 Lumber - Bessemer", "2722208 ALBERTA LTD."):
        assert not J.INSPECTION_PREFIX.match(name), name


def test_company_tokens_see_through_prefix_branch_and_legal_words():
    a = J.company_tokens("WA317940369 - TRUSS COMPANY & BUILDING SUPPLY INC THE")
    b = J.company_tokens("The Truss Company - Pasco")
    assert b == ("truss", "company") and J.same_company(a, b)
    assert J.same_company(J.company_tokens("WA317944963 - LOUWS TRUSS INC"), J.company_tokens("Louws Truss, Inc. - Cashmere"))
    # One generic word is not a company, and a generic name matches only where a name starts.
    assert not J.same_company(("truss",), J.company_tokens("Phoenix Truss LTD"))
    assert not J.same_company(J.company_tokens("TRUSS COMPANY"), J.company_tokens("Ariel Truss Company, Inc."))
    assert not J.same_company(J.company_tokens("TRUSS SYSTEMS, INC."), J.company_tokens("Austin Roof & Truss Systems, Inc."))
    assert J.same_company(J.company_tokens("TRUSS COMPANY"), J.company_tokens("The Truss Company"))


def test_the_truss_company_job_sites_are_proposed():
    out = by_id(PLANTS + JOBSITES)
    gig = out["IC-90838"]
    assert "company_plant_elsewhere" in gig["signals"] and "lot_or_subdivision" in gig["signals"]
    assert "IC-90459:SUMNER" in gig["company_plants"]
    assert out["IC-95868"]["tier"] == "likely"            # a 2,783 sqft house, and the company's plants are elsewhere
    assert out["IC-95981"]["signals"] == "company_plant_elsewhere no_building_found"
    assert out["IC-95981"]["tier"] == "review"            # no building found at all: Overture may have missed it
    assert "company_plant_elsewhere" in out["IC-90872"]["signals"]   # Louws: Ferndale, Cashmere


def test_a_company_with_no_plant_on_record_is_proposed_on_building_evidence():
    out = by_id(PLANTS + JOBSITES)
    assert out["IC-90564"]["signals"] == "no_plant_scale_building"
    assert out["IC-90564"]["tier"] == "review"
    assert "IC-90862" in out
    # 15,537 sqft stands near 4622 Tamarisk and Trus Way has no plant record: nothing to say.
    assert "IC-90512" not in out


def test_pacific_woodtech_is_not_proposed():
    assert "IC-90264" not in by_id(PLANTS + JOBSITES + [PACIFIC_WOODTECH])


def test_a_plant_record_is_never_proposed_however_small_its_building():
    # Louws Ferndale: 4,950 sqft at the point, but nothing marks it an inspection record.
    assert not set(by_id(PLANTS)) & {p["facility_id"] for p in PLANTS}


def test_a_plant_scale_building_at_the_point_rules_it_a_plant():
    row = g("IC-1", "WA317900000 - THE TRUSS COMPANY & BUILDING SUPPLY LLC", "9 Mill Rd", "YAKIMA",
            inspection_only=1, max_sqft=60000, point_sqft=60000, review="contains_point")
    assert by_id(PLANTS + [row]) == {}


def test_an_inspection_at_the_companys_own_plant_is_not_elsewhere():
    row = g("IC-2", "WA317900001 - THE TRUSS COMPANY & BUILDING SUPPLY LLC", "2802 142nd Avenue East", "SUMNER",
            inspection_only=1, max_sqft=357321, review="nearest_largest")
    assert by_id(PLANTS + [row]) == {}


def test_the_loader_runs_on_the_warehouse_schema(tmp_path):
    wh = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    with wh.transaction() as c:
        c.execute("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) VALUES ('IC-1', 'active', NULL, 't', 't')")
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, address, city, state) VALUES "
                  "('IC-1', 'v1+reg.a', 'WA317976402 - THE TRUSS COMPANY & BUILDING SUPPLY LLC', '4497 WANDERING WAY', 'PORT ORCHARD', 'WA')")
        c.execute("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                  "source_class, basis, site_visit) VALUES ('a1', 'v1+reg.a', 'IC-1', 'osha_inspections', 'address', "
                  "'4497 WANDERING WAY', 'B', 'none', 1)")
        c.execute("INSERT INTO facility_building_review (facility_key, point, overture_release, outcome, evaluated_at) "
                  "VALUES ('IC-1', '47.5,-122.6', 'x', 'none', 't')")
    rows = J.load(wh)
    assert rows[0]["inspection_only"] in (1, True)
    out = J.candidates(rows)
    assert [c["facility_id"] for c in out] == ["IC-1"]
    assert (out[0]["signals"], out[0]["tier"]) == ("no_building_found", "review")
    J.write_csv(out, tmp_path / "w.csv")
    assert (tmp_path / "w.csv").read_text().startswith(",".join(J.COLUMNS))
    wh.close()


def test_a_source_only_inspection_record_needs_two_signals():
    # Louws Truss's real Ferndale plant: EPA FRS files it from OSHA alone, and the company has other
    # plants. One signal is not enough without the inspection-number prefix in its name.
    ferndale = dict(PLANTS[3], inspection_only=1)
    assert "IC-90744" not in by_id([ferndale] + PLANTS[4:])
    # The Truss Company row with no prefix, at a house in Puyallup: two signals, proposed.
    puyallup = g("IC-90681", "TRUSS COMPANY", "1413 FIRLAND DR", "PUYALLUP", inspection_only=1,
                 max_sqft=3603, review="nearest_largest")
    assert by_id(PLANTS + [puyallup])["IC-90681"]["tier"] == "likely"
