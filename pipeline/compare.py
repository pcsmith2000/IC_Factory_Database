"""Compare two releases — e.g. the GitHub run against the Cowork prototype.

Drift between the fixed process and the prototype should be a number, not a feeling.
Reports: facility count, per-state counts, tier mix, ids only in A / only in B, and rows whose
facility assignment differs. Facilities are joined on signature (not id) so two builds with
independent id registries can still be compared.
"""
from __future__ import annotations
import csv, json
from collections import defaultdict
from pathlib import Path


def _load(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def compare(a_dir: Path, b_dir: Path) -> dict:
    fa, fb = _load(a_dir / "facilities.csv"), _load(b_dir / "facilities.csv")
    sa = {f["signature"]: f for f in fa}
    sb = {f["signature"]: f for f in fb}
    only_a, only_b = sorted(set(sa) - set(sb)), sorted(set(sb) - set(sa))
    def by_state(fs):
        d = defaultdict(int)
        for f in fs: d[f["state"]] += 1
        return dict(d)
    def by_tier(fs):
        d = defaultdict(int)
        for f in fs: d[f["tier"]] += 1
        return dict(d)
    sta, stb = by_state(fa), by_state(fb)
    state_delta = {s: stb.get(s, 0) - sta.get(s, 0) for s in set(sta) | set(stb) if stb.get(s, 0) != sta.get(s, 0)}
    return {
        "a": str(a_dir), "b": str(b_dir),
        "facilities": {"a": len(fa), "b": len(fb), "delta": len(fb) - len(fa)},
        "shared_signatures": len(set(sa) & set(sb)),
        "only_in_a": len(only_a), "only_in_b": len(only_b),
        "only_in_a_sample": [sa[s]["name"] for s in only_a[:20]],
        "only_in_b_sample": [sb[s]["name"] for s in only_b[:20]],
        "state_delta": dict(sorted(state_delta.items(), key=lambda kv: -abs(kv[1]))),
        "tiers": {"a": by_tier(fa), "b": by_tier(fb)},
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(compare(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
