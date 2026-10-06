"""New facilities by pull request: a plant no source lists is minted once, reaches golden, and stays there."""
import csv

import pytest

from pipeline import facility_intake as I, golden_refresh as gr, warehouse
from pipeline.registry import load_yaml

NOW = "v1+reg.a+ids.00000000+ctl.x"
NEXT = "v1+reg.b+ids.00000000+ctl.y"
LEAF, GROUP = "Wood Volumetric Modular", "Modular"


def line(**kw):
    r = {"intake_id": "NF-acme-modular-tracy-ca", "name": "Acme Modular", "address": "100 Industrial Way",
         "city": "Tracy", "state": "CA", "zip": "95304", "website": "https://acme.example", "phone": "209-555-0100",
         "capability_group": GROUP, "capability_leaf": LEAF, "sq_ft": "120000", "lat_lon": "",
         "retrieved_date": "2026-10-06", "issue": "https://github.com/pcsmith2000/IC_Factory_Database/issues/140",
         "evidence": "Plant page https://acme.example/plants/tracy shows the Tracy factory and its 120,000 sq ft",
         "proposed_by": "research agent (wr-new-1)", "not_duplicate_of": ""}
    r.update(kw)
    return r


def write(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=I.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


@pytest.fixture
def wh(tmp_path):
    w = warehouse.SqliteWarehouse(tmp_path / "w.sqlite")
    with w.transaction() as c:
        c.execute("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                  "VALUES ('IC-00001', 'active', NULL, 't', 't')")
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, 'IC-00001', 'tx_tdlr', ?, ?, 'A', 'on_current_list', '2026-09-21')",
                      [("r1", NOW, "name", "Lone Star Modular"), ("r2", NOW, "state", "TX"),
                       ("r3", NOW, "address", "5 Oak Rd"), ("r4", NOW, "city", "Austin")])
        c.execute("INSERT INTO golden_facility (facility_key, release_tag, name, name__source) VALUES ('IC-00001', ?, 'x', 'x')", (NOW,))
    gr.refresh(w, all_facilities=True)
    yield w
    w.close()


def golden(wh):
    return {r["facility_key"]: r for r in wh.query("SELECT * FROM golden_facility")}


def test_the_empty_file_in_the_repo_is_valid():
    assert I.problems() == []


def test_a_line_must_be_complete_and_cited(tmp_path):
    p = write(tmp_path / "n.csv", [line(intake_id="acme", state="California", capability_group="Panel",
                                        evidence="trust me", phone="12")])
    bad = "\n".join(I.problems(p))
    for want in ("intake_id 'acme' is not valid", "two-letter US state", "belongs to group 'Modular'",
                 "evidence must cite", "10-digit"):
        assert want in bad
    p = write(tmp_path / "n.csv", [line(), line()])
    assert any("repeats line 2" in x for x in I.problems(p))


def test_a_new_plant_is_minted_once_and_reaches_golden(wh, tmp_path):
    p = write(tmp_path / "n.csv", [line()])
    out = I.apply(wh, path=p)
    fid = out["minted"][0]["facility_id"]
    assert fid.startswith("IC-") and out["written"] == 10            # every non-blank field
    again = I.apply(wh, path=p)
    assert again["minted"] == [] and again["written"] == 0 and again["existing"][0]["facility_id"] == fid
    assert wh.query("SELECT kind FROM facility_event WHERE facility_id = ?", (fid,))[0]["kind"] == "mint"
    gr.refresh(wh)
    row = golden(wh)[fid]
    assert (row["name"], row["city"], row["state"], row["capability_leaf"], row["name__source"]) == \
        ("Acme Modular", "Tracy", "CA", LEAF, "facility_intake")


def test_the_plant_survives_a_new_release(wh, tmp_path):
    fid = I.apply(wh, path=write(tmp_path / "n.csv", [line()]))["minted"][0]["facility_id"]
    gr.refresh(wh)
    # A full run loads a new release that lists IC-00001 again but knows nothing of the new plant.
    with wh.transaction() as c:
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, 'IC-00001', 'tx_tdlr', ?, ?, 'A', 'on_current_list', '2026-10-07')",
                      [("n1", NEXT, "name", "Lone Star Modular"), ("n2", NEXT, "state", "TX")])
        c.execute("UPDATE golden_facility SET release_tag = ?", (NEXT,))
    gr.refresh(wh, all_facilities=True)
    assert fid in golden(wh) and golden(wh)[fid]["name"] == "Acme Modular"


def test_a_registry_listing_corrects_a_founding_fact_but_not_the_model(wh, tmp_path):
    fid = I.apply(wh, path=write(tmp_path / "n.csv", [line()]))["minted"][0]["facility_id"]
    with wh.transaction() as c:
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-10-01')",
                      [("x1", NOW, fid, "ca_hcd", "address", "100 Industrial Way Ste B", "A", "on_current_list"),
                       ("x2", NOW, fid, "capability", "capability_leaf", "Wood Panelized (Open)", "capability", "model_capability")])
    gr.refresh(wh)
    row = golden(wh)[fid]
    assert row["address"] == "100 Industrial Way Ste B"              # a registry outranks the founding fact
    assert row["capability_leaf"] == LEAF                            # the cited capability outranks the model


def test_a_look_alike_is_refused_unless_cleared(wh, tmp_path):
    same_name = line(intake_id="NF-lone-star", name="Lone Star Modular, Inc.", address="9 Elm St", city="Waco", state="TX")
    same_addr = line(intake_id="NF-oak", name="Oak Builders", address="5 Oak Road", city="Austin", state="TX")
    out = I.apply(wh, path=write(tmp_path / "n.csv", [same_name, same_addr]))
    assert [r["looks_like"] for r in out["refused"]] == [["IC-00001"], ["IC-00001"]] and out["minted"] == []
    out = I.apply(wh, path=write(tmp_path / "n.csv", [dict(same_addr, not_duplicate_of="IC-00001")]))
    assert len(out["minted"]) == 1


def test_dry_run_mints_and_writes_nothing(wh, tmp_path):
    p = write(tmp_path / "n.csv", [line()])
    out = I.apply(wh, path=p, dry_run=True)
    assert out["minted"][0]["facility_id"] is None and out["written"] == 10
    assert wh.query("SELECT count(*) AS n FROM facility")[0]["n"] == 1
    assert I.apply(wh, path=p)["written"] == 10


def test_an_employee_created_plant_also_survives_a_new_release(wh):
    with wh.transaction() as c:
        c.execute("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                  "VALUES ('IC-96900', 'active', NULL, 't', 'adl_viz:Pat')")
        c.execute("INSERT INTO employee_feedback (feedback_id, facility_key, employee_name, auth_method, channel, note, "
                  "changes_json, created_at, request_hash, creates_facility) VALUES "
                  "('f1', 'IC-96900', 'Pat', 'shared_passcode', 'field_edit', 'new plant', '[]', '2026-10-06T00:00:00Z', 'h', 1)")
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key, row_hash) VALUES (?, ?, 'IC-96900', 'adl_employee_feedback', ?, ?, "
                      "'human_feedback', 'human_verified', '2026-10-06', 'f1')",
                      [("f1:name", NOW, "name", "Pat's Plant"), ("f1:state", NOW, "state", "OH")])
        c.execute("UPDATE golden_facility SET release_tag = ?", (NEXT,))
        c.executemany("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                      "source_class, basis, date_key) VALUES (?, ?, 'IC-00001', 'tx_tdlr', ?, ?, 'A', 'on_current_list', '2026-10-07')",
                      [("n1", NEXT, "name", "Lone Star Modular"), ("n2", NEXT, "state", "TX")])
    gr.refresh(wh, all_facilities=True)
    assert golden(wh)["IC-96900"]["name"] == "Pat's Plant"


def test_a_carried_correction_alone_does_not_resurrect_a_dropped_plant(wh):
    with wh.transaction() as c:
        c.execute("INSERT INTO fact_assertions (assertion_id, release_tag, facility_key, source_key, field_key, value, "
                  "source_class, basis, date_key) VALUES ('o1', ?, 'IC-00001', 'operator', 'zip', '78701', 'operator', 'operator', '2026-10-01')", (NOW,))
    rows, _, _ = gr.compute(wh, ["IC-00001"], NEXT, load_yaml(gr.ROOT / "registry" / "survivorship.yaml"))
    assert rows == {}                                                # NEXT asserts nothing about IC-00001
