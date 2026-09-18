"""IC_AI=off promises a run that completes without a model. corporate_locations broke that promise:
acquire lets an ai_extraction source through when ANY transcribed CSV is in its folder, but parse
uses those CSVs per company, so an un-transcribed page still called the model and halted the run."""
import pipeline.sources.corporate_locations as cl


def _page(tmp_path, company, body):
    d = tmp_path / company; d.mkdir(parents=True, exist_ok=True)
    p = d / "locations.html"; p.write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    return p


def _csv(tmp_path, company):
    p = tmp_path / f"{company}.csv"
    p.write_text("name,address,city,state,zip,kind,evidence,source_url\n"
                 "Acme Truss Plant,1 Mill Rd,Dublin,GA,31021,plant,manufacturing,https://x/1\n", encoding="utf-8")
    return p


def test_an_untranscribed_page_is_recorded_as_unread_rather_than_calling_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("IC_AI", "off")
    def boom(*a, **k):
        raise AssertionError("extract_locations must not be called with IC_AI=off")
    monkeypatch.setattr(cl, "extract_locations", boom)

    paths = [_csv(tmp_path, "stark-truss"), _page(tmp_path, "banker-steel", "plant text " * 60)]
    rows = cl.parse(paths, {"id": "corporate_locations", "url": "x", "pages": []}, cfg={})

    assert [r["name_verbatim"] for r in rows] == ["Acme Truss Plant"]   # the hand-read rows survive
    audit = (tmp_path / "extraction_audit.json")
    assert audit.exists() and "IC_AI=off" in audit.read_text()          # and the gap is on the record


def test_with_ai_on_the_same_page_still_goes_to_the_model(tmp_path, monkeypatch):
    monkeypatch.delenv("IC_AI", raising=False)
    called = []
    monkeypatch.setattr(cl, "extract_locations", lambda text, **k: called.append(k["company"]) or
                        {"locations": [], "dropped": [], "model": "m", "prompt_hash": "h"})
    cl.parse([_page(tmp_path, "banker-steel", "plant text " * 60)],
             {"id": "corporate_locations", "url": "x", "pages": []}, cfg={})
    assert called == ["banker steel"]
