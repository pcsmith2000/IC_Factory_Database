from pipeline.recovery.fl_bcis import detail_links,manufacturing_address
from pipeline.sources.fl_bcis import parse


def test_results_keep_opaque_detail_link_and_identifier():
    body='''<a id="grdReport__ctl2_hlnkOrgName" href="mb_orgapp_dtl2.aspx?param=abc%2B1">Acme &amp; Co</a>
    <b>FBC Organization Number</b> MFT-12'''
    assert detail_links(body)==[{'name':'Acme & Co','source_identifier':'MFT-12',
        'url':'https://floridabuilding.org/mb/mb_orgapp_dtl2.aspx?param=abc%2B1'}]


def test_only_manufacturing_facility_controls_are_used():
    body='''<label id="lblMFFacility">Manufacturing Facility</label>
      <input id="txtStreetAddr1_txtTextbox" value="PO Box 9">
      <input id="txtStreetAddr2_txtTextbox" value="14915 US 27 S.">
      <input id="txtCity_txtTextbox" value="Lake Wales">
      <select id="lstMFState_drpCustomDropdown"><option value="AL">Alabama</option><option selected="selected" value="FL">Florida</option></select>
      <input id="txtZip_txtTextbox" value="33859">'''
    assert manufacturing_address(body)=={'address':'14915 US 27 S.','city':'Lake Wales','state':'FL','zip':'33859'}


def test_mailing_address_without_physical_facility_is_rejected():
    body='<input id="cOrgInfo_txtOrgAddressline1_txtTextbox" value="10 Office Rd">'
    assert manufacturing_address(body) is None


def test_normal_ingestion_preserves_exact_detail_citation(tmp_path):
    page=tmp_path/'results.html'
    page.write_text('''<div id="grdReport">
      <a href="mb_orgapp_dtl2.aspx?param=opaque%2Bvalue" id="grdReport__ctl2_hlnkOrgName">Acme Modules</a>
      <b>FBC Organization Number</b> MFT-12
      <span id="grdReport__ctl2_lblOrgStatus">Active</span>
      <span id="grdReport__ctl2_lblValidToDate">12/31/2027</span>
      <span id="grdReport__ctl2_lblAdministrator">Jane Doe (850) 555-0100</span>
    </div>''')
    rows=parse([page],{'id':'fl_bcis'})
    assert rows[0]['source_url']=='https://floridabuilding.org/mb/mb_orgapp_dtl2.aspx?param=opaque%2Bvalue'
    assert rows[0]['source_identifier']=='MFT-12'
