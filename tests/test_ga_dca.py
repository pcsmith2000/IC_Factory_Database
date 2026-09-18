from pipeline.sources.ga_dca import _addr_text, _split_address, TAG


def test_the_address_column_is_judged_without_the_email_that_shares_it():
    """"1300 Davenport Drive Minden, LA 71055 engineering@fibrebond.com" does not end in a ZIP, so
    Fibrebond never read as complete and Frey-Moss Structures was glued onto it."""
    assert _addr_text("1300 Davenport Drive Minden, LA 71055 engineering@fibrebond.com".split()) == "1300 Davenport Drive Minden, LA 71055"
    assert _addr_text("http:// 12140 Vance Davis Drive Charlotte, NC 28269".split()) == "12140 Vance Davis Drive Charlotte, NC 28269"


def test_the_records_own_tag_decides_where_the_city_starts():
    m = _split_address("1035 Iris Drive SE Conyers, GA 30094", "Conyers")
    assert (m.group("street"), m.group("city")) == ("1035 Iris Drive SE", "Conyers")
    m = _split_address("5031 Hazel Jones Road Bossier City, LA 71111", "Bossier City")
    assert (m.group("street"), m.group("city")) == ("5031 Hazel Jones Road", "Bossier City")
    # tag city differs from the plant's city (HQ Willacoochee, plant Douglas): fallback keeps the street whole
    m = _split_address("1299 Thompson Drive Douglas, GA 31535", "Willacoochee")
    assert (m.group("street"), m.group("city")) == ("1299 Thompson Drive", "Douglas")


def test_a_lowercase_state_in_the_tag_still_reads():
    assert TAG.search("Mustard Seed Tiny Homes (Buford, Ga)").group(2).upper() == "GA"
