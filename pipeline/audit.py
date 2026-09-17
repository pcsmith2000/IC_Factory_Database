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
    ("doors_windows",   r"\bmasonite\b|\bjeld[\s\-]?wen\b|\bandersen\s+window|\banderson\s+window|\bpella\b|\bmarvin\s+window"),
    ("commodity_panel", r"\blouisiana[\s\-]pacific\b|\bgeorgia[\s\-]?pacific\b|\bweyerhaeuser\b|\bplywood\b|\bveneer\b|\bparticle\s*board\b|\bosb\b"),
    ("millwork",        r"\bmillwork\b|\bmoulding\b|\bmolding\b|\bcabinet(ry|s)?\b|\bcountertop"),
    ("pallets_crates",  r"\bpallet\b|\bpallets\b|\bcrating\b"),
    ("concrete_supply", r"\bready[\s\-]?mix\b|\bredi[\s\-]?mix\b|\baggregate(s)?\b"),
    ("infrastructure",  r"\bculvert\b|\bseptic\b|\bburial\s+vault|\bconcrete\s+pipe\b"),
]

# A name with one of these reads as a genuine IC plant even when a flagged word is also present,
# so it is reported apart rather than counted against the classifier.
IC_SIGNALS = re.compile(
    r"\btruss(es)?\b|\bmodular\b|\bmanufactured\s+hom|\bmobile\s+hom|\bprefab|\bpre[\s\-]?engineered\b"
    r"|\bpanelized\b|\bpanelised\b|\bwall\s+panel|\bfloor\s+panel|\broof\s+panel|\bsip\b"
    r"|\bglulam\b|\bcross[\s\-]laminated\b|\bmass\s+timber\b|\bmetal\s+building|\bbuilding\s+systems?\b",
    re.I)

_COMPILED = [(cat, re.compile(pat, re.I)) for cat, pat in NOT_IC_PATTERNS]


def scan(names: list[str]) -> dict:
    """Audit a list of admitted establishment names.

    Returns the floor count, the breakdown by category, the ambiguous names held back, and a
    sample of each so a reviewer can check the patterns rather than trust them.
    """
    by_cat: dict[str, list[str]] = {}
    ambiguous: list[str] = []
    for nm in names:
        if not nm:
            continue
        hit = next((cat for cat, rx in _COMPILED if rx.search(nm)), None)
        if not hit:
            continue
        if IC_SIGNALS.search(nm):
            ambiguous.append(nm)
        else:
            by_cat.setdefault(hit, []).append(nm)
    flagged = sum(len(v) for v in by_cat.values())
    total = len([n for n in names if n])
    return {
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
    return (f"  precision audit: {res['flagged']} of {res['admitted']} admitted names are known "
            f"non-IC manufacturing = {res['floor_fp_rate']:.1%} FLOOR on false positives "
            f"({cats}); {res['ambiguous_held_back']} ambiguous held back")
