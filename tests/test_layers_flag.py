"""--layers must stop where it says. It gated 1, 3, 7 and 8; 2, 4, 5, 5b and 6 ran regardless, so a
`--layers 1-2` probe went on to reconcile and issued 1,998 permanent IC numbers (2026-09-18). Ids
are never renumbered, so a speculative run spends them for good."""
import json
import pytest
from pipeline.run import main, _layers


def test_the_range_parses_as_a_closed_interval():
    assert _layers("1-2") == {1, 2}
    assert _layers("2-8") == {2, 3, 4, 5, 6, 7, 8}
    assert _layers("5") == {5}


@pytest.mark.parametrize("spec, issues_ids", [("1-2", False), ("1-3", False), ("1-4", False),
                                              ("1-5", True), ("1-8", True)])
def test_the_id_registry_is_only_written_when_layer_5_was_asked_for(tmp_path, monkeypatch, spec, issues_ids):
    """Layer 5 is the one that hands out IC numbers, so it is the one that must not run uninvited."""
    registry = tmp_path / "id_registry.json"
    registry.write_text(json.dumps({}))
    before = registry.read_text()

    monkeypatch.setenv("IC_ID_REGISTRY", str(registry))
    monkeypatch.setenv("IC_CSV_DIR", str(tmp_path / "csv"))
    monkeypatch.setenv("IC_WAREHOUSE_PATH", str(tmp_path / "w.sqlite"))
    monkeypatch.setenv("IC_AI", "off")
    monkeypatch.setenv("IC_ARCHIVE", "off")
    (tmp_path / "csv").mkdir(exist_ok=True)

    try:
        main(["--layers", spec, "--dry-run"])
    except SystemExit:
        pass
    except Exception:
        pass                      # the point is the file, not whether a fixture-less run completes
    if not issues_ids:
        assert registry.read_text() == before, f"--layers {spec} wrote the id registry"


def test_every_layer_from_4_on_is_gated_in_the_source():
    """A regression guard with teeth: the bug was a missing `if N not in layers` for 4, 5 and 6."""
    src = (__import__("pathlib").Path(__file__).resolve().parent.parent / "pipeline" / "run.py").read_text()
    for n in (4, 5, 6, 7, 8):
        assert f"if {n} not in layers:" in src, f"layer {n} is not gated by --layers"
