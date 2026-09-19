"""RUNLOG.csv is the trajectory, one row per run, and since 2026-09-18 the industry-size test is
the only measurement in it that has a denominator nobody in this project controls.

Recall went when the control list became a source (registry/config.yaml -> control). What replaced
it was already running beside it: Census CBP establishment counts for the core NAICS codes, by
state. Reading it a run at a time out of the JSON records is not a trajectory, so it belongs in
the columns."""
import json
from pathlib import Path
from pipeline.runlog import row_for

RECORD = {
    "started": "2026-09-18T23:52:28+00:00",
    "layers": {"7_measure": {"coverage_bias": {
        "counted_facilities": 3839, "frame_total": 2750, "mean_abs_bias": 0.215,
        "out_of_band": {"MS": 0.4, "WV": 2.1}, "states": {}}}},
}


def _row(tmp_path, record):
    p = tmp_path / "2026-09-18T235228.json"
    p.write_text(json.dumps(record))
    return row_for(p)


def test_the_frame_numbers_reach_the_log(tmp_path):
    r = _row(tmp_path, RECORD)
    assert (r["frame_total"], r["counted"], r["mean_abs_bias"]) == (2750, 3839, "0.215")
    assert r["bias_outliers"] == 2


def test_coverage_above_one_is_reported_as_measured(tmp_path):
    """The config treats the CBP total as a FLOOR — the codes do not cover every kind of IC plant —
    so 140% is a real reading, not an error to clamp."""
    assert _row(tmp_path, RECORD)["coverage"] == "1.396"


def test_a_run_that_measured_nothing_leaves_the_columns_blank(tmp_path):
    """Layer 1 halts, or --layers stops short: writing a zero there would read as a measurement."""
    r = _row(tmp_path, {"started": "x", "layers": {}, "halted_at": "layer 1"})
    assert [r[k] for k in ("frame_total", "counted", "coverage", "mean_abs_bias", "bias_outliers")] \
           == ["", "", "", "", ""]
