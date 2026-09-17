from pathlib import Path
from pipeline.sources.forest_products_locator import _cards, parse, KEEP

CARD = ('<p class="millTitle"> <span><a title="click for more details..." '
        'href="https://secondary.forestproductslocator.org/manufacturers/{slug}">{name}</a> </span> </p> '
        '<p> <span>{street}<br />{csz}<br /></span> <a target="_blank" href="https://maps.google.com?q=1,2">'
        'Map This Location</a><br> <span class="label">Species:</span> Pine<br> </p> <hr>')

PAGE = "<html><body>" + "".join(CARD.format(**c) for c in [
    dict(slug="north-georgia-truss-co", name="North Georgia Truss Co.",
         street="1279 Joe Frank Harris Pkwy.", csz="Cartersville, GA 30120"),
    dict(slug="a-b-woodcraft-inc", name="A &amp; B Woodcraft, Inc.",
         street="2346 Mellon Ct", csz="Decatur, GA 30035"),
    dict(slug="gilmer-building-components-inc", name="Gilmer Building Components, Inc.",
         street="PO Box 1", csz="Ellijay, GA 30540"),
    dict(slug="mock-pallet-co-inc", name="Mock Pallet Co., Inc.",
         street="175 Cook Road", csz="Covington, GA 30014"),
]) + "</body></html>"


def test_every_card_reads_with_its_street_and_city_state_zip():
    cards = _cards(PAGE)
    assert [c["name"] for c in cards] == ["North Georgia Truss Co.", "A & B Woodcraft, Inc.",
                                          "Gilmer Building Components, Inc.", "Mock Pallet Co., Inc."]
    assert cards[0]["street"] == "1279 Joe Frank Harris Pkwy."
    assert (cards[0]["city"], cards[0]["state"], cards[0]["zip"]) == ("Cartersville", "GA", "30120")


def test_only_names_that_say_what_they_make_are_kept(tmp_path: Path):
    (tmp_path / "GA.html").write_text(PAGE, encoding="utf-8")
    rows = parse([tmp_path / "GA.html"], {"id": "forest_products_locator", "url": "x"})
    assert [r["name_verbatim"] for r in rows] == ["North Georgia Truss Co.", "Gilmer Building Components, Inc."]
    assert rows[0]["address_verbatim"] == "1279 Joe Frank Harris Pkwy." and rows[0]["state_verbatim"] == "GA"
    assert "2 kept of 4 cards" in rows[0]["notes"] and "PARTIAL" in rows[0]["notes"]


def test_the_keep_filter_takes_trusses_and_leaves_cabinets():
    assert KEEP.search("Quality-Bilt Trusses, Inc.") and KEEP.search("Southland Log Homes, Inc.")
    assert KEEP.search("Trussway Manufacturing, Inc.") and KEEP.search("Georgia Mountain Components, Inc.")
    assert not KEEP.search("Cook Cabinet Shop Inc.") and not KEEP.search("Custom Pallet Co.")
    assert not KEEP.search("Panolam Industries, Inc.")


def test_a_transient_404_is_retried_and_a_persistent_one_leaves_the_state_out(monkeypatch, tmp_path):
    import urllib.error
    from pipeline.sources import forest_products_locator as fpl
    calls = {"n": 0}

    def flaky(url, archive_dir, filename, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        p = archive_dir / filename; p.write_text(PAGE); return p

    monkeypatch.setattr(fpl, "http_get", flaky)
    monkeypatch.setattr(fpl.time, "sleep", lambda s: None)
    assert fpl._get_state("FL", tmp_path) is not None and calls["n"] == 3

    def down(url, archive_dir, filename, **kw):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(fpl, "http_get", down)
    assert fpl._get_state("VA", tmp_path) is None
