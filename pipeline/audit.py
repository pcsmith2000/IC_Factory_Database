"""Pool precision audit: how many admitted plants are things we know are not IC.

G5 scores the classifier against `control/seeds.csv` — 30 IC and 30 NOT-IC, balanced and
hand-picked. That measures the boundaries the seeds encode and nothing else. Release
v1.0.0+reg.22389a4 scored 100% precision on those seeds while admitting at least 113 plants no
reading of the scope allows: Masonite and JELD-WEN (doors and windows), Louisiana-Pacific and
Georgia-Pacific (OSB and plywood), veneer mills, pallet plants, ready-mix. The seeds could not
see them because nothing that looks like a plywood mill is in the seed set.

This module is the cheap, deterministic counterweight. It does not classify and it never drops a
row: it counts admitted establishments whose names match well-known non-IC manufacturing, and
reports that count as a FLOOR on the false-positive rate. Only names are matched, because a floor
built from unambiguous national brands and unambiguous product words is one nobody has to argue
about — the true rate is higher, since Oldcastle, Panelfold and the rest match nothing here.

A name carrying a strong IC signal as well as a flagged word ("Pacific Truss & Plywood") is
counted separately and excluded from the floor: those are the cases a keyword can't call.

    python -m pipeline.audit build/classify_cache --rows build/facilities.csv
"""
from __future__ import annotations
import re

# Each entry: (category, pattern). Patterns are deliberately narrow — a national brand whose
# entire business is the flagged product, or a product word with no IC reading.
NOT_IC_PATTERNS: list[tuple[str, str]] = [
    # Plurals matter more than they look: "FLORIDA PLYWOODS, INC." went uncounted against
    # \bplywood\b because the boundary fails on the trailing s, so the floor measured on runs 13
    # and 14 was an undercount. Every product noun here takes an optional s.
    ("doors_windows",   r"\bmasonite\b|\bjeld[\s\-]?wen\b|\bandersen\s+window|\banderson\s+window|\bpella\b|\bmarvin\s+window"),
    ("commodity_panel", r"\blouisiana[\s\-]pacific\b|\bgeorgia[\s\-]?pacific\b|\bweyerhaeuser\b|\bplywoods?\b|\bveneers?\b|\bparticle\s*boards?\b|\bosb\b"),
    ("millwork",        r"\bmillworks?\b|\bmouldings?\b|\bmoldings?\b|\bcabinet(ry|s)?\b|\bcountertop"),
    ("pallets_crates",  r"\bpallets?\b|\bcrating\b|\bcrates?\b"),
    ("concrete_supply", r"\bready[\s\-]?mix\b|\bredi[\s\-]?mix\b|\baggregates?\b"),
    ("infrastructure",  r"\bculverts?\b|\bseptic\b|\bburial\s+vaults?\b|\bconcrete\s+pipes?\b"),
]

# A name with one of these reads as a genuine IC plant even when a flagged word is also present,
# so it is reported apart rather than counted against the classifier.
IC_SIGNALS = re.compile(
    r"\btruss(es)?\b|\bmodular\b|\bmanufactured\s+hom|\bmobile\s+hom|\bprefab|\bpre[\s\-]?engineered\b"
    r"|\bpanelized\b|\bpanelised\b|\bwall\s+panel|\bfloor\s+panel|\broof\s+panel|\bsip\b"
    r"|\bglulam\b|\bcross[\s\-]laminated\b|\bmass\s+timber\b|\bmetal\s+building|\bbuilding\s+systems?\b",
    re.I)

_COMPILED = [(cat, re.compile(pat, re.I)) for cat, pat in NOT_IC_PATTERNS]


def scan(names: list[str], product_types: list[str] | None = None) -> dict:
    """Audit a list of admitted establishment names.

    Returns the floor count, the breakdown by category, the ambiguous names held back, and a
    sample of each so a reviewer can check the patterns rather than trust them.

    Pass product_types alongside to get the floor per IC product category, which is where this
    stops being a scalar and starts being a diagnosis. On run 35174109197 the 9.0% floor was not
    spread evenly at all: panel 23.6% and other 19.1% carried almost all of it, while hud_code
    (0.2%), metal_building (0.0%), precast (0.0%), volumetric (1.2%) and truss_component (1.5%)
    were clean. That is the wood-products sense of "panel" — a sheet of material — colliding with
    the IC sense, a wall panel, and it says which part of the prompt to fix.
    """
    by_cat: dict[str, list[str]] = {}
    ambiguous: list[str] = []
    pt_total: dict[str, int] = {}
    pt_flagged: dict[str, int] = {}
    types = product_types or [None] * len(names)
    for nm, pt in zip(names, types):
        if not nm:
            continue
        if pt:
            pt_total[pt] = pt_total.get(pt, 0) + 1
        hit = next((cat for cat, rx in _COMPILED if rx.search(nm)), None)
        if not hit:
            continue
        if IC_SIGNALS.search(nm):
            ambiguous.append(nm)
        else:
            by_cat.setdefault(hit, []).append(nm)
            if pt:
                pt_flagged[pt] = pt_flagged.get(pt, 0) + 1
    flagged = sum(len(v) for v in by_cat.values())
    total = len([n for n in names if n])
    by_product = {pt: {"admitted": n, "flagged": pt_flagged.get(pt, 0),
                       "rate": round(pt_flagged.get(pt, 0) / n, 4) if n else 0.0}
                  for pt, n in sorted(pt_total.items(), key=lambda x: -x[1])}
    return {
        "by_product_type": by_product,
        "admitted": total,
        "flagged": flagged,
        "floor_fp_rate": round(flagged / total, 4) if total else 0.0,
        "by_category": {k: len(v) for k, v in sorted(by_cat.items(), key=lambda x: -len(x[1]))},
        "examples": {k: sorted(v)[:5] for k, v in by_cat.items()},
        "ambiguous_held_back": len(ambiguous),
        "ambiguous_examples": sorted(ambiguous)[:5],
    }


def line(res: dict) -> str:
    """One line for the run log. Says floor, never 'the' false-positive rate."""
    if not res.get("admitted"):
        return "  precision audit: nothing admitted by the classifier — nothing to audit"
    cats = ", ".join(f"{k} {v}" for k, v in res["by_category"].items()) or "none"
    out = (f"  precision audit: {res['flagged']} of {res['admitted']} admitted names are known "
           f"non-IC manufacturing = {res['floor_fp_rate']:.1%} FLOOR on false positives "
           f"({cats}); {res['ambiguous_held_back']} ambiguous held back")
    worst = [f"{pt} {d['rate']:.0%}" for pt, d in res.get("by_product_type", {}).items() if d["rate"] >= 0.05]
    if worst:
        out += f"\n    concentrated in: {', '.join(worst)}"
    return out
