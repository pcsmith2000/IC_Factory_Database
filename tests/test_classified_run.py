"""The classified path, end to end, with the model stubbed out.

Every other test here is a unit, and the deterministic IC_AI=off run skips Layer 3 entirely — so
the path that actually produces a release had no automated coverage at all. Three of the defects
found on 2026-09-17 lived on exactly this path: product_type was computed and discarded, the
precision audit had nothing to run against, and review_queue.csv reached a human without the
classifier's own reasoning. A stub for classify.run exercises all of it offline.
"""
import csv
import os
from pathlib import Path
import pytest
from pipeline import classify, run as run_mod

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def classified(tmp_path, monkeypatch):
    """Run the pipeline with a canned classifier and hand back the build directory."""
    seen: dict = {}

    def fake_run(rows, cfg, seeds, cache_dir, prompt_path, hb=None):
        seen["n"] = len(rows)
        labels = {}
        for i, r in enumerate(rows):
            nm = (r.get("name_verbatim") or "").upper()
            if "PLYWOOD" in nm or "MILLWORK" in nm:
                labels[r["row_hash"]] = {"row_hash": r["row_hash"], "label": "NOT-IC",
                                         "confidence": 0.9, "type": "none",
                                         "reason": "commodity panel stock"}
            elif i % 7 == 0:
                labels[r["row_hash"]] = {"row_hash": r["row_hash"], "label": "UNCERTAIN",
                                         "confidence": 0.4, "type": "none",
                                         "reason": "record too thin to decide"}
            else:
                labels[r["row_hash"]] = {"row_hash": r["row_hash"], "label": "IC",
                                         "confidence": 0.95, "type": "truss_component",
                                         "reason": "roof truss plant"}
        # Same keys classify.run returns — run.py reads n_candidates to decide whether G5 has
        # anything to score, and a stub that drops a key tests the stub, not the pipeline.
        # classify.run labels the seeds too — they ride along in the batches — and G5 halts the
        # run if they come back unlabelled, which it duly did the first time this stub omitted
        # them. Score them correctly so the gate passes on its own terms.
        for sd in seeds:
            labels[sd["row_hash"]] = {"row_hash": sd["row_hash"], "label": sd["seed_label"],
                                      "confidence": 0.99, "type": "none", "reason": "seed"}
        return {"labels": labels, "model": "stub/test", "provider": "stub", "temperature": 0,
                "seeds_also_candidates": 0, "prompt_hash": "deadbeef",
                "n_candidates": len(rows), "n_seeds": len(seeds)}

    monkeypatch.setattr(classify, "run", fake_run)
    monkeypatch.setattr(run_mod, "ai_enabled", lambda: True)
    # The run refuses to start with AI on and no key, which is correct — it is the check that
    # stopped a run from silently producing an unclassified "release". The stub stands in for the
    # provider so the rest of the path can be exercised without one.
    monkeypatch.setattr(run_mod, "ai_client_and_model", lambda m: (None, "stub/test", "stub"))
    monkeypatch.setenv("IC_WAREHOUSE_ENGINE", "sqlite")
    monkeypatch.setenv("IC_WAREHOUSE_PATH", str(tmp_path / "w.sqlite"))
    monkeypatch.setenv("IC_ARCHIVE", "off")
    # Seven hand-written contract rows instead of the 100k in ic-csv/: the point is that the path
    # runs, not that it runs at scale, and a fixture keeps this a test rather than a five-minute
    # rebuild of the whole release.
    monkeypatch.setenv("IC_CSV_DIR", str(ROOT / "tests" / "fixtures" / "ic-csv"))
    # Ids are never renumbered, so a fixture run against the real registry permanently assigns IC
    # numbers to plants that do not exist. It took IC-94453 through IC-94456 once; never again.
    monkeypatch.setenv("IC_ID_REGISTRY", str(tmp_path / "id_registry.json"))
    out = tmp_path / "build"
    rc = run_mod.main(["--layers", "2-8", "--out", str(out)])
    assert rc == 0, f"pipeline exited {rc}"
    assert seen.get("n"), "the classifier was never called — Layer 3 did not run"
    return out


def test_product_type_reaches_the_golden_table(classified):
    """It was declared in three places and asserted in none."""
    golden = list(csv.DictReader(open(classified / "golden.csv")))
    assert golden, "no golden rows"
    typed = [g for g in golden if g.get("product_type")]
    assert typed, "product_type is empty for every facility — the Layer 3 output is being dropped"
    assert {g["product_type"] for g in typed} == {"truss_component"}
    assert {g["product_type__source"] for g in typed} == {"classifier"}


def test_review_queue_carries_the_classifier_reasoning(classified):
    """A reviewer decides each UNCERTAIN row and must not be handed less than the pipeline had."""
    rows = list(csv.DictReader(open(classified / "review_queue.csv")))
    assert rows, "no UNCERTAIN rows produced by the stub"
    assert {"ic_label", "ic_confidence", "ic_type", "ic_reason"} <= set(rows[0])
    assert all(r["ic_label"] == "UNCERTAIN" for r in rows)
    assert all(r["ic_reason"] == "record too thin to decide" for r in rows)


def test_not_ic_rows_do_not_reach_the_release(classified):
    names = {(r.get("name") or "").upper() for r in csv.DictReader(open(classified / "golden.csv"))}
    assert not [n for n in names if "PLYWOOD" in n or "MILLWORK" in n]


def test_the_real_id_registry_is_untouched_by_a_fixture_run(classified):
    """Guards the mistake this fixture made once: fixture rows took IC-94453 through IC-94456 in
    the tracked registry, and ids are never renumbered, so the burn would have been permanent."""
    import json
    real = json.loads((ROOT / "id_registry.json").read_text())
    assert not any(sig.endswith(("700 ash blvd", "300 elm rd", "600 birch way", "200 oak ave"))
                   for sig in real["ids"]), "a fixture address reached the real id registry"
