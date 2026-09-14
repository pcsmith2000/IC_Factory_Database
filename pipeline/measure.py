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
    """Crosswalk links first; otherwise name+state match. Out-of-scope rows are excluded."""
    in_scope = [c for c in control_rows if c.get("triage") in {"in_scope_locatable", "in_scope_no_location"}]
    fac_ids = {f["facility_id"] for f in facilities}
    by_name_state = {(norm_name(f["name"]), f["state"]): f["facility_id"] for f in facilities}
    hits, by_method = 0, defaultdict(int)
    for c in in_scope:
        cw = crosswalk.get(c["control_id"])
        if cw and cw in fac_ids:
            hits += 1; by_method["crosswalk"] += 1; continue
        if (norm_name(c["name"]), (c.get("state") or "").upper()) in by_name_state:
            hits += 1; by_method["name+state"] += 1
    return {"in_scope": len(in_scope), "found": hits, "recall": hits / len(in_scope) if in_scope else 0.0,
            "by_method": dict(by_method)}


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
