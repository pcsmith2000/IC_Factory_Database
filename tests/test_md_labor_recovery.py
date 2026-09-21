from pipeline.recovery.md_labor import parse_page,unique_addresses


def test_fixed_width_pdf_table_extracts_only_physical_streets():
    page='''Plant ID                                                               Contact Person             Number_Street
P-Apex Homes of PA, LLC                                                Kyle Nornhold              7172 Route 522
P-Diamond Builders, Inc. Plant 2                                                                  428 Simmons Dr
P-Modular Connections                                                  Chad Pearson
P-Mail Only                                                            Jane Doe                   PO Box 8
'''
    assert parse_page(page)==[
        {'name':'Apex Homes of PA, LLC','address':'7172 Route 522'},
        {'name':'Diamond Builders, Inc. Plant 2','address':'428 Simmons Dr'}]


def test_ambiguous_names_are_not_matched():
    rows=[{'name':'A','address':'1 Main St'},{'name':'A','address':'2 Main St'},
          {'name':'B','address':'3 Plant Rd'},{'name':'B','address':'3 Plant Rd'}]
    assert unique_addresses(rows)=={'b':{'name':'B','address':'3 Plant Rd'}}
