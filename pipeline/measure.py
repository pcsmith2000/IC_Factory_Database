"""Layer 7: measure and defend. Reported, not steered.

recall on the held-out control · per-state coverage vs CBP · bias index per state.
A state outside the band is published with its cause from registry/known-gaps.yaml; the run
never goes looking for a source to fix it.
"""
from __future__ import annotations
import csv
from collections import defaultdict
from pathlib import Path
from .reconcile import norm_name


def load_frame(path: Path) -> dict[str, int]:
    """CSV: state,establishments — Census CBP state totals for the four core codes."""
    out = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out[r["state"].upper()] = int(r["establishments"])
    return out


def recall(control_rows: list[dict], facilities: list[dict], crosswalk: dict[str, str]) -> dict:
    """Did the pipeline find the establishments a human already verified exist?

    Three matchers, strongest first: an explicit crosswalk link, then name+state, then NAME ALONE.

    The name-only rung is the point of the control list, not a concession to it. The list is a set
    of companies somebody checked; the question it answers is "is this name in the database", and
    requiring a state made a names-only list score 0% — indistinguishable from the pipeline having
    missed every one of them, which is the same false-zero that `recall: 0.0` was reporting before
    2026-09-17. A control row with a state still uses it, because it is stronger evidence.

    A name matching facilities in more than one state is counted as found and reported separately:
    the list says the company exists, and one of those rows is it, but which one is not established.

    Out-of-scope rows are excluded. A row with no `triage` value is treated as in scope and
    counted, so a bare name list works with no triage column at all — with the untriaged count
    reported, because "we assumed all 241 were in scope" is a claim the reader should see.
    """
    IN_SCOPE = {"in_scope_locatable", "in_scope_no_location"}
    in_scope, untriaged = [], 0
    for c in control_rows:
        t = (c.get("triage") or "").strip()
        if not t:
            untriaged += 1; in_scope.append(c)
        elif t in IN_SCOPE:
            in_scope.append(c)
    fac_ids = {f["facility_id"] for f in facilities}
    by_name_state: dict[tuple, str] = {}
    by_name: dict[str, set] = defaultdict(set)
    for f in facilities:
        by_name_state[(norm_name(f["name"]), (f.get("state") or "").upper())] = f["facility_id"]
        by_name[norm_name(f["name"])].add(f["facility_id"])
    hits, by_method, misses = 0, defaultdict(int), []
    for c in in_scope:
        nm = norm_name(c.get("name", ""))
        cw = crosswalk.get(c.get("control_id", ""))
        if cw and cw in fac_ids:
            hits += 1; by_method["crosswalk"] += 1; continue
        if (nm, (c.get("state") or "").upper()) in by_name_state:
            hits += 1; by_method["name+state"] += 1; continue
        if nm and nm in by_name:
            hits += 1
            by_method["name" if len(by_name[nm]) == 1 else "name (ambiguous: several states)"] += 1
            continue
        misses.append(c.get("name", ""))
    if not in_scope:
        # control/control-triaged.csv is empty, so there is nothing to have found. Reporting 0.0
        # states that the pipeline missed every establishment it was asked about, which is both
        # false and the most damning number in the release row — fact_release_metrics.recall has
        # carried it on every release to date. None says "not measured", the same distinction
        # GateResult.tested draws for G2 and G3.
        return {"in_scope": 0, "found": 0, "recall": None, "tested": False,
                "note": "no control rows triaged in_scope — recall cannot be measured, and 0.0 "
                        "would read as a total miss rather than an absent test",
                "by_method": {}}
    return {"in_scope": len(in_scope), "found": hits, "recall": hits / len(in_scope),
            "tested": True, "by_method": dict(by_method), "untriaged_assumed_in_scope": untriaged,
            "missed": sorted(misses)[:50], "n_missed": len(misses)}


def coverage_and_bias(facilities: list[dict], frame: dict[str, int], band: tuple[float, float], min_share: float) -> dict:
    counted = [f for f in facilities if f["tier"] != "T0"]
    ours = defaultdict(int)
    for f in counted:
        ours[f["state"]] += 1
    n_ours, n_frame = len(counted), sum(frame.values())
    states = {}
    out_of_band = {}
    for st, est in frame.items():
        share_frame = est / n_frame
        share_ours = ours.get(st, 0) / n_ours if n_ours else 0.0
        bias = share_ours / share_frame if share_frame else 0.0
        states[st] = {"ours": ours.get(st, 0), "frame": est, "coverage": ours.get(st, 0) / est if est else 0.0, "bias": round(bias, 2)}
        if share_frame >= min_share and not (band[0] <= bias <= band[1]):
            out_of_band[st] = round(bias, 2)
    mab = sum(abs(s["bias"] - 1) for st, s in states.items() if frame[st] / n_frame >= min_share)
    n = sum(1 for st in states if frame[st] / n_frame >= min_share)
    return {"counted_facilities": n_ours, "frame_total": n_frame, "states": states,
            "out_of_band": out_of_band, "mean_abs_bias": round(mab / n, 3) if n else None}
