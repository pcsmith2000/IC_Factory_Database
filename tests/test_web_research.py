"""Page helpers shared by research_eval's Fetcher and coordinate recovery (pipeline/web_research/pages.py)."""
import pytest
from pipeline.web_research.pages import safe_url


@pytest.mark.parametrize('url',['file:///etc/passwd','http://127.0.0.1','http://169.254.169.254','https://user:pw@example.com','http://[::1]'])
def test_private_and_credentialed_urls_blocked(url):
    with pytest.raises(ValueError): safe_url(url)


def test_public_encoded_contact_is_decoded_without_running_script():
    from bs4 import BeautifulSoup
    from pipeline.web_research.pages import decode_contact_spans
    text='Telephone: 908-561-3484'
    alphabet=''.join(sorted(set(text)))
    encoded=''.join(chr(alphabet.index(c)+48) for c in text)
    soup=BeautifulSoup(f'<span id="contact"></span><script>var ml="{alphabet}",mi="{encoded}";document.getElementById("contact");throw new Error("do not execute")</script>','html.parser')
    decode_contact_spans(soup)
    assert soup.find(id='contact').get_text()==text


def test_redacted_search_link_does_not_abort_facility():
    from pipeline.web_research.pages import domain
    assert domain('https://[link removed]')==''
    assert domain('https://www.example.com/contact')=='example.com'


def test_public_contact_email_decoding_preserves_exact_addresses():
    from bs4 import BeautifulSoup
    from pipeline.web_research.pages import decode_public_email_links
    email='office@example.com';key=42
    encoded=bytes([key]+[ord(c)^key for c in email]).hex()
    soup=BeautifulSoup(f'<span data-cfemail="{encoded}">[email protected]</span><a href="mailto:plant%40example.com?subject=Hello">Email us</a><span data-cfemail="bad">Bad</span>','html.parser')
    decode_public_email_links(soup)
    assert email in soup.get_text()
    assert 'plant@example.com' in soup.get_text()
    assert 'subject=Hello' not in soup.get_text()


def test_rooftop_requires_matching_street_not_only_house_number():
    from pipeline.recovery.streets import street_matches
    assert street_matches('123 North First Street Suite 2',{'formatted_street':'N First St','unit_number':'2'})
    assert street_matches('5301 Polk St 20',{'formatted_street':'Polk St','unit_number':'20'})
    assert not street_matches('123 First St',{'formatted_street':'Second St'})
    assert not street_matches('831 New York 67 Bldg 46',{'formatted_street':'Church Ave','unit_number':'46'})
