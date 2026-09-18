from pathlib import Path
from pipeline.sources.mbma import parse

CARD = ('<div class="member"> <div class="info"> <h4><strong>Behlen Building Systems</strong></h4> '
        '<p>4025 East 23rd Street</p> <p>Columbus, Nebraska 68602-0569</p> <a href="tel:402-562-4192">x</a> '
        '</div> <div class="links"> <a href="http://www.behlenbuildingsystems.com/" target="_blank">'
        'behlenbuildingsystems.com</a> <a class="action-button" href="#">See Profile</a> </div></div>')


def test_the_member_website_on_the_card_reaches_the_contract_row(tmp_path: Path):
    p = tmp_path / "building-systems.html"; p.write_text("<html>" + CARD + "</html>", encoding="utf-8")
    rows = parse([p], {"id": "mbma", "url": "x"})
    assert rows[0]["name_verbatim"] == "Behlen Building Systems"
    assert rows[0]["website"] == "http://www.behlenbuildingsystems.com/"
    assert (rows[0]["address_verbatim"], rows[0]["city_verbatim"], rows[0]["state_verbatim"]) == \
        ("4025 East 23rd Street", "Columbus", "NE")
