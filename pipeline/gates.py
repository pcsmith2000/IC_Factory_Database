"""Layer 6: five quality gates. Each returns a GateResult; any failure halts the run.

Gates block, they do not advise. G1 reports duplicate pairs for a person to review — it never
merges. G2 is the mirror image and is not yet built: it fails loudly rather than pretending.
"""
from __future__ import annotations
import csv
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from .registry import sha256_file
from .reconcile import norm_name


@dataclass
class GateResult:
    gate: str
    passed: bool
    summary: str
    details: dict = field(default_factory=dict)


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- G1
def g1_dedupe(facilities: list[dict], max_rate: float, thresholds: dict, out_csv: Path) -> GateResult:
    """Three detectors, three confidence tiers. Writes every pair for review; merges nothing."""
    pairs = []
    by_state: dict[str, list[dict]] = defaultdict(list)
    for f in facilities:
        by_state[f["state"]].append(f)
    for st, fs in by_state.items():
        by_street: dict[str, list[dict]] = defaultdict(list)
        for f in fs:
            if f.get("street_key"):
                by_street[f["street_key"]].append(f)
        for key, group in by_street.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    pairs.append((group[i], group[j], thresholds["street_key"], "same street key + state"))
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                a, b = fs[i], fs[j]
                if a.get("street_key") and a.get("street_key") == b.get("street_key"):
                    continue
                na, nb = norm_name(a["name"]), norm_name(b["name"])
                if not na or not nb:
                    continue
                if na == nb and _sim(a["city_norm"], b["city_norm"]) >= 0.85:
                    pairs.append((a, b, thresholds["name_city"], "identical name + near-identical city"))
                elif _sim(na, nb) >= 0.90 and _sim(a["city_norm"], b["city_norm"]) >= 0.85:
                    pairs.append((a, b, thresholds["fuzzy"], "fuzzy name + fuzzy city"))
    # merge groups via union-find over pairs ≥ 0.90 → collapses
    parent = {f["facility_id"]: f["facility_id"] for f in facilities}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for a, b, conf, _ in pairs:
        if conf >= 0.90:
            parent[find(a["facility_id"])] = find(b["facility_id"])
    groups = defaultdict(list)
    for f in facilities:
        groups[find(f["facility_id"])].append(f["facility_id"])
    collapses = sum(len(g) - 1 for g in groups.values() if len(g) > 1)
    rate = collapses / len(facilities) if facilities else 0.0
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["confidence", "signal", "id_a", "name_a", "city_a", "id_b", "name_b", "city_b", "decision"])
        for a, b, conf, sig in sorted(pairs, key=lambda p: -p[2]):
            w.writerow([conf, sig, a["facility_id"], a["name"], a["city_norm"], b["facility_id"], b["name"], b["city_norm"], ""])
    return GateResult("G1 dedupe audit", rate <= max_rate,
                      f"{len(pairs)} pairs, {collapses} collapses = {rate:.1%} (max {max_rate:.0%}); corrected count {len(facilities) - collapses}",
                      {"pairs": len(pairs), "collapses": collapses, "rate": rate, "corrected_count": len(facilities) - collapses,
                       "review_csv": str(out_csv)})


# ---------------------------------------------------------------- G2
def g2_false_merge(rows: list[dict]) -> GateResult:
    """A cluster holding two irreconcilable street keys in one city is a false merge.
    Built as a detector over the reconciled rows; flagged clusters fail the gate."""
    by_fac: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r.get("street_key"):
            by_fac[r["facility_id"]].add(r["street_key"])
    flagged = {fid: sorted(keys) for fid, keys in by_fac.items() if len(keys) > 1}
    return GateResult("G2 false-merge check", not flagged,
                      f"{len(flagged)} clusters with >1 street key", {"flagged": flagged})


# ---------------------------------------------------------------- G3
def g3_id_stability(ids_issued: int, allow_new: int, is_rerun: bool) -> GateResult:
    """On a re-run over identical inputs, zero new ids and zero renumbering."""
    if not is_rerun:
        return GateResult("G3 id stability", True, f"first run: {ids_issued} ids issued (not a re-run; stability not tested)")
    return GateResult("G3 id stability", ids_issued <= allow_new, f"re-run issued {ids_issued} new ids (allowed {allow_new})")


# ---------------------------------------------------------------- G4
def g4_control_isolation(control_path: Path, checksum_path: Path, rows: list[dict]) -> GateResult:
    if not control_path.exists() or not checksum_path.exists():
        return GateResult("G4 control isolation", False, "control file or checksum missing")
    actual = sha256_file(control_path)
    expected = checksum_path.read_text().split()[0]
    leaked = [r for r in rows if (r.get("source_id") or "").startswith("control")]
    ok = actual == expected and not leaked
    return GateResult("G4 control isolation", ok,
                      f"checksum {'ok' if actual == expected else 'MISMATCH'}; {len(leaked)} rows with control provenance")


# ---------------------------------------------------------------- G5
def g5_classifier_eval(labels: dict[str, dict], seeds: list[dict], min_p: float, min_r: float) -> GateResult:
    tp = fp = fn = tn = 0
    for s in seeds:
        lab = labels.get(s["row_hash"], {}).get("label")
        truth = s["seed_label"]
        if truth == "IC" and lab == "IC": tp += 1
        elif truth == "IC": fn += 1
        elif lab == "IC": fp += 1
        else: tn += 1
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    if not seeds:
        return GateResult("G5 classifier eval", False, "no seeds — the AI step is unaudited")
    return GateResult("G5 classifier eval", p >= min_p and r >= min_r,
                      f"precision {p:.0%} (min {min_p:.0%}), recall {r:.0%} (min {min_r:.0%}), n={len(seeds)}",
                      {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r})
