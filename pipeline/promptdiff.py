"""Compare two runs' classifier output row by row.

G5 scores 60 hand-picked seeds. It cannot see a change that moves rows the seeds do not contain,
and on 2026-09-17 it missed one in each direction on the same pair of runs:

  prompt v1.1 -> v1.2   G5 precision ROSE 96% -> 100% and recall held at 87%, while the classifier
                        quietly stopped admitting ~90 genuine core-code plants — CMH Manufacturing
                        and Clayton Wakarusa (HUD-code), Deltec Homes, Pacific Wall Systems.
  the same change       removed 539 millwork, window, door and commodity-panel rows, which G5 also
                        could not see, because nothing resembling a plywood mill is a seed.

The gate was not wrong; it was answering a different question. This answers the one that matters
when a prompt changes: which rows moved, and what are they. Run it on the classify_cache of two
runs over identical input — the cache key includes the prompt hash, so the two directories are
directly comparable by row_hash.

    python -m pipeline.promptdiff OLD_CACHE NEW_CACHE --rows OLD_RUN/build/normalised
"""
from __future__ import annotations
import csv, glob, json
from collections import Counter
from pathlib import Path

# NAICS families whose rows are IC by default. A drop here is a candidate loss and worth a name;
# a drop from millwork, window, door or commodity-panel families is the intended effect.
CORE_NAICS = {
    "321991": "manufactured homes", "321992": "prefab wood buildings",
    "321214": "truss", "321213": "engineered wood members",
    "332311": "prefab metal buildings", "327390": "precast",
}
PRODUCT_NAICS = {"321911", "321918", "321912", "321219", "321211", "321212", "321920"}


def load_labels(cache_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in glob.glob(str(Path(cache_dir) / "*.json")):
        for o in json.loads(Path(f).read_text()):
            if isinstance(o, dict) and o.get("row_hash"):
                out[o["row_hash"]] = o
    return out


def load_names(normalised_dir: Path) -> dict[str, tuple[str, str]]:
    """row_hash -> (name, naics) from a run's normalised contract rows."""
    out: dict[str, tuple[str, str]] = {}
    for f in glob.glob(str(Path(normalised_dir) / "*.csv")):
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("row_hash"):
                    out[r["row_hash"]] = (r.get("name_verbatim", ""), r.get("naics_verbatim", ""))
    return out


def diff(old: dict[str, dict], new: dict[str, dict], names: dict[str, tuple[str, str]] | None = None) -> dict:
    names = names or {}
    common = set(old) & set(new)
    moves: Counter = Counter()
    dropped, gained = [], []
    for h in common:
        a, b = old[h].get("label"), new[h].get("label")
        if a == b:
            continue
        moves[f"{a} -> {b}"] += 1
        nm, naics = names.get(h, ("", ""))
        if a == "IC" and b != "IC":
            dropped.append({"row_hash": h, "name": nm, "naics": naics, "reason": new[h].get("reason", "")})
        elif a != "IC" and b == "IC":
            gained.append({"row_hash": h, "name": nm, "naics": naics})
    core_lost = Counter(d["naics"] for d in dropped if d["naics"] in CORE_NAICS)
    core_won = Counter(g["naics"] for g in gained if g["naics"] in CORE_NAICS)
    return {
        "rows_compared": len(common),
        "moves": dict(moves.most_common()),
        "dropped": dropped, "gained": gained,
        "product_naics_dropped": sum(1 for d in dropped if d["naics"] in PRODUCT_NAICS),
        "core_naics_dropped": sum(core_lost.values()),
        "core_naics_gained": sum(core_won.values()),
        "core_net": sum(core_won.values()) - sum(core_lost.values()),
        "core_by_code": {n: {"lost": core_lost.get(n, 0), "won": core_won.get(n, 0)} for n in CORE_NAICS},
    }


def render(d: dict, sample: int = 15) -> str:
    out = [f"{d['rows_compared']} rows compared",
           f"  moves: {d['moves']}",
           f"  dropped {len(d['dropped'])}  gained {len(d['gained'])}",
           f"  from product NAICS (millwork/window/door/panel stock): -{d['product_naics_dropped']}  "
           f"— the intended effect",
           f"  from core IC NAICS: -{d['core_naics_dropped']} +{d['core_naics_gained']} "
           f"= net {d['core_net']:+d}  — candidate losses"]
    for n, c in d["core_by_code"].items():
        if c["lost"] or c["won"]:
            out.append(f"     {n} {CORE_NAICS[n]:24} -{c['lost']:<4} +{c['won']}")
    losses = [x for x in d["dropped"] if x["naics"] in CORE_NAICS]
    if losses:
        out.append("  core-code rows the newer prompt dropped:")
        for x in sorted(losses, key=lambda x: x["name"])[:sample]:
            out.append(f"     {x['name'][:44]:44} {x['naics']:8} {x['reason'][:40]}")
    return "\n".join(out)


def main(argv=None) -> int:
    import argparse, sys
    ap = argparse.ArgumentParser(prog="python -m pipeline.promptdiff")
    ap.add_argument("old_cache"); ap.add_argument("new_cache")
    ap.add_argument("--rows", help="a run's build/normalised dir, to name the rows")
    ap.add_argument("--sample", type=int, default=15)
    a = ap.parse_args(argv)
    d = diff(load_labels(Path(a.old_cache)), load_labels(Path(a.new_cache)),
             load_names(Path(a.rows)) if a.rows else {})
    print(render(d, a.sample))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
