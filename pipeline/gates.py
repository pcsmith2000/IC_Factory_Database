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
    tested: bool = True
    """False when the gate could not assert anything. It still does not halt the run, but it
    must not read as evidence. Across 27 run records G2, G3 and G4 passed 14 times each and
    asserted nothing on any of them: G2 cannot find a false merge while Layer 4 is a stub and
    nothing merges, G3 says "not a re-run" unless --rerun is passed. A vacuous pass is worse
    than a skip, because a reader counts it."""


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- G1
def g1_dedupe(facilities: list[dict], max_rate: float, thresholds: dict, out_csv: Path,
              target_rate: float | None = None) -> GateResult:
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
                ka, kb = a.get("street_key"), b.get("street_key")
                if ka and ka == kb:
                    continue                       # already paired by the street detector above
                na, nb = norm_name(a["name"]), norm_name(b["name"])
                if not na or not nb:
                    continue
                name_city = na == nb and _sim(a["city_norm"], b["city_norm"]) >= 0.85
                fuzzy = _sim(na, nb) >= 0.90 and _sim(a["city_norm"], b["city_norm"]) >= 0.85
                if not (name_city or fuzzy):
                    continue
                if ka and kb:
                    # Both sides carry a street address and the addresses are DIFFERENT. A company
                    # with several plants in one city is the normal case, not a duplicate: TAS
                    # Energy has five Houston sites and TXLA Systems five in Huffman, and every one
                    # of the 58 "duplicate" pairs among located facilities in the 2026-09-17 run
                    # was this shape — the entire 3.8% was false. Two known, different addresses
                    # are evidence of two establishments. Still reported, because normalisation can
                    # split one plant ("100 Main St" / "100 N Main Street"), but below the collapse
                    # threshold so it is a review item and not a correction.
                    pairs.append((a, b, thresholds.get("same_name_other_street", 0.40),
                                  "same name + city, DIFFERENT street — distinct sites unless "
                                  "normalisation split one"))
                elif name_city:
                    pairs.append((a, b, thresholds["name_city"], "identical name + near-identical city"))
                else:
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
    det = {"pairs": len(pairs), "collapses": collapses, "rate": rate,
           "corrected_count": len(facilities) - collapses, "review_csv": str(out_csv)}
    # The ceiling this gate runs against is a temporary accommodation for the stub resolver. Say
    # so on every run: across 27 run records the rate has been 4.0-4.4% against a ceiling of 10%,
    # and every one of those runs would fail the 2% this gate was actually calibrated to. A debt
    # that is only recorded in a config comment stops being visible.
    debt = ""
    if target_rate is not None and rate > target_rate:
        det["target_rate"] = target_rate
        det["would_fail_target"] = True
        debt = (f"; ABOVE the {target_rate:.0%} target this gate is calibrated to — passing only "
                f"because the ceiling is relaxed while Layer 4 is a stub")
    return GateResult("G1 dedupe audit", rate <= max_rate,
                      f"{len(pairs)} pairs, {collapses} collapses = {rate:.1%} (max {max_rate:.0%}); "
                      f"corrected count {len(facilities) - collapses}{debt}", det)


# ---------------------------------------------------------------- G2
def g2_false_merge(rows: list[dict], out_csv: Path | None = None,
                   max_rate: float = 1.0, target_rate: float | None = None,
                   min_sim: float = 0.60) -> GateResult:
    """Two different businesses sharing one facility id.

    This used to look for a cluster holding more than one street key, and reported "0 false merges
    in 177 merged clusters" on every run. That number was arithmetic, not evidence: a facility's id
    comes from a signature that CONTAINS the street key, so every addressed row in it carries the
    same key by construction and len(keys) > 1 was unreachable. The gate could not fail, and said
    tested=True while saying it — a worse outcome than the honest skip it was meant to avoid.

    The detectable signal under street-key clustering is the opposite one: one address, two
    unrelated NAMES. Industrial parks, shared buildings and a street key that dropped a unit
    designator all put distinct firms on one id. On the 2026-09-17 run 41 of 177 multi-row
    facilities showed it — "spitzer industries" with "volta", "atkinson ind" with "nvent",
    "bizon group" with "conexwest".

    It reports rather than halts, because the same signal covers legitimate aliasing: "smi homes"
    and "structural modular innovations" are one firm, "cmh manufacturing west" and "schult homes"
    are two brands of one Clayton plant, "bildt" is a typo for "boldt". Telling those apart needs a
    person or an entity resolver, which is Layer 4's job and Layer 4 is a stub. So the pairs go to
    a review CSV exactly as G1's do, and the ceiling is honest about being provisional.
    """
    by_fac: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("street_key"):
            by_fac[r["facility_id"]].append(r)
    merged = {fid: rs for fid, rs in by_fac.items() if len(rs) > 1}
    if not merged:
        return GateResult("G2 false-merge check", True,
                          f"untested: no facility was built from more than one addressed row, so "
                          f"no merge could be false ({len(by_fac)} clusters, all single-row)",
                          {"flagged": {}, "merged_clusters": 0}, tested=False)
    # Two signals, and they mean different things. A cluster holding two street keys means the
    # clustering itself broke — ids come from a signature containing the key, so it cannot happen
    # while reconcile works, and if it ever does it is a bug, not a judgement call. That fails
    # outright, at any rate. Unrelated names at ONE key is the data-quality signal, and it is
    # rate-gated because aliasing produces it too.
    structural = {fid: sorted({r["street_key"] for r in rs})
                  for fid, rs in merged.items() if len({r["street_key"] for r in rs}) > 1}
    flagged = {}
    for fid, rs in merged.items():
        names = sorted({norm_name(r.get("name_verbatim", "")) for r in rs if r.get("name_verbatim")})
        if len(names) < 2:
            continue
        worst = min(_sim(a, b) for i, a in enumerate(names) for b in names[i + 1:])
        if worst < min_sim:
            flagged[fid] = {"similarity": round(worst, 2), "names": names,
                            "street_key": rs[0].get("street_key"), "state": rs[0].get("state")}
    rate = len(flagged) / len(merged)
    if out_csv is not None:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["similarity", "facility_id", "state", "street_key", "names", "decision"])
            for fid, d in sorted(flagged.items(), key=lambda x: x[1]["similarity"]):
                w.writerow([d["similarity"], fid, d["state"], d["street_key"], " | ".join(d["names"]), ""])
    debt = ""
    if target_rate is not None and rate > target_rate:
        debt = (f"; ABOVE the {target_rate:.0%} target — aliasing and genuine false merges cannot be "
                f"separated until Layer 4 resolves entities, so every pair needs review")
    det = {"flagged": flagged, "structural": structural, "merged_clusters": len(merged),
           "rate": rate, "target_rate": target_rate,
           "review_csv": str(out_csv) if out_csv else None}
    if structural:
        return GateResult("G2 false-merge check", False,
                          f"{len(structural)} clusters hold more than one street key — the "
                          f"signature that issues a facility id contains that key, so this means "
                          f"clustering is broken, not that the data is messy", det)
    return GateResult("G2 false-merge check", rate <= max_rate,
                      f"{len(flagged)} of {len(merged)} merged clusters hold unrelated names "
                      f"= {rate:.1%} (max {max_rate:.0%}){debt}", det)


# ---------------------------------------------------------------- G3
def g3_id_stability(ids_issued: int, allow_new: int, is_rerun: bool) -> GateResult:
    """On a re-run over identical inputs, zero new ids and zero renumbering."""
    if not is_rerun:
        # Stability is only observable by running the same inputs twice. Without --rerun this
        # gate has nothing to compare against and has never asserted anything in 27 runs.
        return GateResult("G3 id stability", True,
                          f"untested: {ids_issued} ids issued on a first run; stability needs "
                          f"--rerun over identical inputs", {"ids_issued": ids_issued}, tested=False)
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
def g5_classifier_eval(labels: dict[str, dict], seeds: list[dict], min_p: float, min_r: float,
                       base_rate: float | None = None, min_p_at_base: float | None = None) -> GateResult:
    """Score the hidden seeds. Reports precision on the seed set AND at the production base rate.

    The seed set is 30 IC against 30 NOT-IC. The candidate pool is roughly 20% IC. Precision
    measured at 50% prevalence flatters a classifier, because false positives are drawn from the
    negative class and that class is four times larger in production:

        seed precision       = r / (r + f)
        production precision = r / (r + f * (1-b)/b * (n_pos/n_neg))

    On the numbers measured tonight a 93% seed precision implies roughly 77% in production, and
    87% implies roughly 63%. A model can clear a 95% bar here and be materially worse than that
    on the actual pool, so the adjusted figure is reported on every run. It only *gates* when
    g5_min_precision_at_base_rate is set, because choosing that threshold is a policy decision
    about how much false-positive rate the database will tolerate, not a number to invent here.
    """
    tp = fp = fn = tn = 0
    unlabelled = [s["row_hash"] for s in seeds if s["row_hash"] not in labels]
    for s in seeds:
        lab = labels.get(s["row_hash"], {}).get("label")
        truth = s["seed_label"]
        if truth == "IC" and lab == "IC": tp += 1
        elif truth == "IC": fn += 1
        elif lab == "IC": fp += 1
        else: tn += 1
    if unlabelled:
        # A seed the classifier never answered was scoring as a true negative, so a model that
        # silently dropped NOT-IC rows was credited with getting them right. Not answering is not
        # a correct answer; the audit is void if any seed went unlabelled.
        return GateResult("G5 classifier eval", False,
                          f"{len(unlabelled)} of {len(seeds)} seeds came back unlabelled — the audit "
                          f"is incomplete, not passing",
                          {"unlabelled": len(unlabelled), "tp": tp, "fp": fp, "fn": fn, "tn": tn})
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    if not seeds:
        return GateResult("G5 classifier eval", False, "no seeds — the AI step is unaudited")
    det = {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r}
    extra, ok_base = "", True
    n_pos, n_neg = tp + fn, tn + fp
    if base_rate and 0 < base_rate < 1 and n_pos and n_neg and tp:
        # Re-weight the negatives from the seed set's prevalence to the pool's.
        w = (n_pos / n_neg) * ((1 - base_rate) / base_rate)
        p_base = tp / (tp + fp * w)
        det.update({"base_rate": base_rate, "precision_at_base_rate": p_base})
        extra = f"; precision at {base_rate:.0%} base rate ~{p_base:.0%}"
        if min_p_at_base is not None:
            ok_base = p_base >= min_p_at_base
            extra += f" (min {min_p_at_base:.0%})"
    return GateResult("G5 classifier eval", p >= min_p and r >= min_r and ok_base,
                      f"precision {p:.0%} (min {min_p:.0%}), recall {r:.0%} (min {min_r:.0%}), "
                      f"n={len(seeds)}{extra}", det)
