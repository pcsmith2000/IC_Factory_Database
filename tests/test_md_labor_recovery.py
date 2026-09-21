from pipeline.recovery.md_labor import parse_lines,unique_addresses


def test_visual_pdf_table_extracts_only_physical_streets():
    def line(page,*cells):
        return [{'text':text,'x0':x,'x1':x+10,'page':page,'top':top}
                for text,x,top in cells]
    lines=[
        line(1,('Plant',25,40),('ID',50,40),('Contact',293,40),('Person',330,40),('Number_Street',398,40)),
        line(1,('P-Apex',25,60),('Homes',70,60),('of',105,60),('PA,',125,60),('LLC',150,60),('Kyle',293,60),('7172',398,60),('Route',430,60),('522',460,60)),
        line(1,('P-Diamond',25,80),('Plant',90,80),('2',140,80),('428',398,80),('Simmons',430,80),('Dr',480,80)),
        line(1,('P-Modular',25,100),('Connections',80,100),('Chad',293,100)),
        line(1,('P-Mail',25,120),('Only',75,120),('Jane',293,120),('PO',398,120),('Box',420,120),('8',450,120))]
    assert parse_lines(lines)==[
        {'name':'Apex Homes of PA, LLC','address':'7172 Route 522'},
        {'name':'Diamond Plant 2','address':'428 Simmons Dr'}]


def test_ambiguous_names_are_not_matched():
    rows=[{'name':'A','address':'1 Main St'},{'name':'A','address':'2 Main St'},
          {'name':'B','address':'3 Plant Rd'},{'name':'B','address':'3 Plant Rd'}]
    assert unique_addresses(rows)=={'b':{'name':'B','address':'3 Plant Rd'}}
