"""SIPA profiles label their contact lines, and the labels are what make them safe to read."""
from pipeline.sources.sipa import PHONE, WEBSITE

SIDEBAR = (
    '<p><strong>ACME Panel Company</strong><br />1905 W Main St<br />Radford, VA 24141<br />United States</p>'
    '<p class="mb-0">Primary Contact: Mike Myers</p>'
    '<p class="mb-0">Phone: <a href="tel:540-267-9988">540-267-9988</a></p>'
    '<p class="mb-0">Fax: 540-808-0824</p>'
    '<p class="mb-0">Website: <a href="/members/acme-panel-company/view/website/13">acmepanel.com</a></p>'
)


def test_the_fax_one_line_below_is_not_taken_as_the_phone():
    """Same shape, same paragraph class — a bare number search returns the fax."""
    assert PHONE.search(SIDEBAR).group(1) == "540-267-9988"
    assert "808-0824" not in PHONE.search(SIDEBAR).group(1)


def test_the_website_is_the_link_TEXT_because_the_href_is_a_sips_org_redirect():
    """The href counts clicks through sips.org; the member's real domain is what is printed."""
    got = WEBSITE.search(SIDEBAR).group(1)
    assert got == "acmepanel.com"
    assert "sips.org" not in got and "/members/" not in got


def test_a_profile_with_no_contact_block_yields_nothing_rather_than_a_guess():
    bare = '<p><strong>Some Member</strong><br />1 Main St<br />Town, VA 24141<br />United States</p>'
    assert PHONE.search(bare) is None and WEBSITE.search(bare) is None
