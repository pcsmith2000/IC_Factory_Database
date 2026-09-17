import csv
from pathlib import Path
import pytest
from pipeline import control
from pipeline.contract import COLUMNS, row_hash
from pipeline.registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent


def test_unplaced_control_inputs_are_reported_not_hidden():
    probs = control.check(load_yaml(ROOT / "registry" / "config.yaml"))
    # control-triaged.csv holds the 241-row ADL list now, so its EMPTY warning is no longer the
    # thing to assert — that it raises nothing is, because that covers every rule at once: the six
    # columns, unique control_id, non-blank names, two-letter states, and the row counts matching
    # registry/config.yaml. Every row is untriaged, which control.check reports as a note and not
    # a problem: an absent opinion is not an absent input.
    assert not any("control-triaged.csv" in p for p in probs), [p for p in probs if "control-triaged" in p]
    # frame_state_totals.csv is placed now; that it raises nothing covers its own rules — the
    # state,establishments columns, two-letter states, integer counts, and the config floor the
    # total must clear.
    assert not any("frame_state_totals.csv" in p for p in probs), [p for p in probs if "frame_state" in p]
    # the classifier prompt is placed now, so the assertion that earns its keep is that neither it
    # nor the seeds raise anything — control.check is what stands between a half-placed control
    # directory and a release
    assert not any("CLASSIFIER-PROMPT" in p for p in probs), [p for p in probs if "CLASSIFIER" in p]
    # seeds.csv is placed now, so its absence is no longer the thing to assert — that it raises no
    # problem at all is, because that covers every seed rule at once: the columns, the IC/NOT-IC
    # label set, the non-blank fields, status_basis, the >=30/>=30 counts, and the row_hash each
    # seed must carry to be found by G5.
    assert not any("seeds.csv" in p for p in probs), [p for p in probs if "seeds.csv" in p]
    assert not any("sha256" in p and "hashes to" in p for p in probs)   # checksum still matches control-triaged.csv


def test_fix_derives_seed_row_hash_and_rewrites_checksum(tmp_path: Path, monkeypatch):
    # work on a copy of the repo's control/ + prompt so nothing under git changes
    import shutil
    (tmp_path / "control").mkdir(); (tmp_path / "prompts").mkdir(); (tmp_path / "registry").mkdir()
    for f in (ROOT / "control").glob("*"): shutil.copy(f, tmp_path / "control" / f.name)
    shutil.copy(ROOT / "prompts" / "CLASSIFIER-PROMPT.md", tmp_path / "prompts")
    monkeypatch.setattr(control, "ROOT", tmp_path)
    cfg = load_yaml(ROOT / "registry" / "config.yaml")
    with open(tmp_path / "control" / "control-triaged.csv", "a", newline="") as f:
        csv.writer(f).writerow(["C001", "Aura Prefab", "Houston", "TX", "in_scope_locatable", ""])
    seed = {c: "" for c in COLUMNS}; seed.update(source_id="epa_frs", source_url="u", source_document="d", retrieved_date="2026-09-01", row_position="1",
                                              name_verbatim="MODULAR HOMES INC", state_verbatim="PA", status_basis="none", row_hash="", seed_label="IC")
    with open(tmp_path / "control" / "seeds.csv", "a", newline="") as f:
        csv.DictWriter(f, fieldnames=COLUMNS + ["row_hash", "seed_label"]).writerow(seed)
    probs = control.check(cfg)
    assert any("row_hash '' ≠" in p for p in probs) and any("control.sha256" in p for p in probs)
    assert any("total_rows = 241" in p for p in probs)          # count drift against config is a problem, not a silent pass
    control.check(cfg, fix=True)
    probs = control.check(cfg)
    assert not any("row_hash" in p or "sha256" in p for p in probs)
    rows = list(csv.DictReader(open(tmp_path / "control" / "seeds.csv")))
    assert rows[0]["row_hash"] == row_hash(rows[0])


def test_the_blob_copy_of_the_control_list_is_outside_the_acquire_prefix():
    """The ADL list lives at ic-control/ in the store, never under the archive prefix.

    Layer 1 reads `<archive.prefix>/<source_id>/<date>/`. A control list sitting under that prefix
    would be acquirable by adding one line to the source registry, and G4 — which only checks a
    checksum and a source_id spelling — would pass while the pipeline scored recall against its
    own input. Isolation here is the prefix, not the gate.
    """
    cfg = load_yaml(ROOT / "registry" / "config.yaml")
    prefix = cfg["archive"]["prefix"].strip("/")
    assert prefix == "ic-sources"
    assert not "ic-control".startswith(prefix + "/") and "ic-control" != prefix
    readme = (ROOT / "control" / "README.md").read_text()
    assert "ic-control/adl-control-2026-09-17.csv" in readme
