import csv
from pathlib import Path
import pytest
from pipeline import control
from pipeline.contract import COLUMNS, row_hash
from pipeline.registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent


def test_the_repo_state_is_reported_not_hidden():
    """What the committed control/ actually is right now, stated as problems rather than passing quietly."""
    probs = control.check(load_yaml(ROOT / "registry" / "config.yaml"))
    # the list is transcribed (241 rows) but not triaged, so recall cannot be measured yet
    assert any("241 of 241 rows have no triage" in p for p in probs)
    assert any("0 in-scope rows but config control.in_scope_rows = 213" in p for p in probs)
    assert any("seeds.csv: EMPTY" in p for p in probs)
    assert any("frame_state_totals.csv: EMPTY" in p for p in probs)
    assert any("placeholder" in p for p in probs)
    assert not any("sha256" in p and "hashes to" in p for p in probs)   # checksum matches the committed file


def test_untriaged_rows_collapse_to_one_problem_not_one_per_row(tmp_path: Path, monkeypatch):
    """241 identical per-row errors would bury every other finding."""
    import shutil, csv as _csv
    for d in ("control", "prompts"):
        (tmp_path / d).mkdir()
    for f in (ROOT / "control").glob("*"):
        shutil.copy(f, tmp_path / "control" / f.name)
    shutil.copy(ROOT / "prompts" / "CLASSIFIER-PROMPT.md", tmp_path / "prompts")
    monkeypatch.setattr(control, "ROOT", tmp_path)
    probs = control.check(load_yaml(ROOT / "registry" / "config.yaml"))
    assert sum(1 for p in probs if "rows have no triage" in p) == 1
    assert not any("triage ''" in p for p in probs), "no per-row triage errors once the column is wholly empty"


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
