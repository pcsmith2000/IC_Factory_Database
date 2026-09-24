"""The legacy crosswalk: every IC-number ever asserted -> the permanent facility it meant (#42).

Epic #39. Before the permanent registry (pipeline/facility_registry.py), each pipeline run minted
IC-numbers into its own copy of id_registry.json, so one number means different plants in
different releases: the warehouse holds 21 releases built from 17 registries, and 11,006 distinct
numbers for about 6,400 plants. fact_assertions is append-only and is never rewritten; this module
records, beside it, which permanent facility each (registry, number) meant.

Resolution is per (registry hash, number), not per release. Within one registry a number always
names one signature and so one plant, so every release built from that registry is pooled as
evidence. In order, first match wins:

    current        the number in the current golden release's own registry, and a permanent id
    same_id        its identity (name, city, state) is shared with the permanent facility of the
                   SAME number: the number always meant this plant
    identity       its identity is shared with exactly one permanent facility (another number)
    unique_name    one of its names belongs to exactly one permanent facility, in the same state,
                   one side names no city, and one side names no street address or they share one
                   (a different city or street is evidence of a second plant: TrueNorth Steel's
                   Fargo plant, "4401 Main Ave." with no city, is not its Mandan plant)
    address        a numbered street address in its state stands at exactly one permanent facility
    unresolved     none of the above: facility_id NULL. The number is retired, never reissued

Ambiguity never resolves: two candidates is unresolved, because a false merge is worse than a gap.

    python -m pipeline.legacy_ids crosswalk [--dry-run]     # main only (workflow facility-registry)
"""
from __future__ import annotations
import json, re, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIDENCE = {"current": 1.0, "same_id": 0.95, "identity": 0.9, "address": 0.8, "unique_name": 0.7,
              "unresolved": None}


def registry_hash(release_tag: str) -> str:
    """The id-registry hash a release tag carries (`+ids.<hash>+`); '' when it carries none."""
    m = re.search(r"\+ids\.([0-9a-f]+)", release_tag or "")
    return m.group(1) if m else ""


def _norm(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def _identity(rows) -> tuple[dict, dict, dict, dict]:
    """(names, keys, addresses, cities), each keyed by (facility_id, registry hash).

    keys: every normalised (name, city, state) combination; addresses: every (numbered street
    address, state). Several sources spell a name or a city differently; each combination is a
    key and two sides name the same plant when any key is shared."""
    parts: dict[tuple[str, str], dict[str, set[str]]] = {}
    for r in rows:
        f, v = r["field"], _norm(r["value"])
        if f not in ("name", "city", "state", "address") or not v:
            continue
        if f == "address" and not re.match(r"^\s*\d", str(r["value"])):
            continue                      # a street with no house number does not name a parcel
        parts.setdefault((r["facility_id"], registry_hash(r["release_tag"])), {}).setdefault(f, set()).add(v)
    names, keys, addresses = {}, {}, {}
    cities = {k: p["city"] for k, p in parts.items() if p.get("city")}
    for k, p in parts.items():
        states = p.get("state") or {""}
        if "name" in p:
            names[k] = {(n, s) for n in p["name"] for s in states}
            keys[k] = {(n, c, s) for n in p["name"] for c in (p.get("city") or {""}) for s in states}
        if "address" in p:
            addresses[k] = {(a, s) for a in p["address"] for s in states}
    return names, keys, addresses, cities


def resolve(pairs: list[tuple[str, str]], identity_rows: list[dict], current_tag: str,
            permanent: set[str]) -> list[dict]:
    """One row per (registry hash, legacy id) in `pairs` [(facility_id, release_tag), ...]. Pure."""
    h0 = registry_hash(current_tag)
    names, keys, addresses, cities = _identity(identity_rows)
    by_key: dict[tuple, set[str]] = {}
    by_name: dict[str, set[tuple[str, str]]] = {}     # name -> {(state, facility)}
    by_addr: dict[tuple, set[str]] = {}
    for (fid, h), ks in keys.items():
        if h == h0 and fid in permanent:
            for k in ks:
                by_key.setdefault(k, set()).add(fid)
    for (fid, h), ns in names.items():
        if h == h0 and fid in permanent:
            for n, st in ns:
                by_name.setdefault(n, set()).add((st, fid))
    for (fid, h), ads in addresses.items():
        if h == h0 and fid in permanent:
            for a in ads:
                by_addr.setdefault(a, set()).add(fid)

    def one(index: dict, evidence) -> set[str]:
        out: set[str] = set()
        for e in evidence:
            out |= index.get(e, set())
        return out

    out = []
    for fid, h in sorted({(f, registry_hash(t)) for f, t in pairs}):
        target, method = None, "unresolved"
        if h == h0 and fid in permanent:
            target, method = fid, "current"
        else:
            cands = one(by_key, keys.get((fid, h), ()))
            if fid in cands:
                target, method = fid, "same_id"
            elif len(cands) == 1:
                target, method = next(iter(cands)), "identity"
            elif not cands:
                # A unique name: in the same state (or either side stateless), exactly one plant.
                named = set()
                for n, s in names.get((fid, h), ()):
                    hit = {f for s2, f in by_name.get(n, ()) if s2 == s or not s or not s2}
                    if len(hit) == 1:
                        named |= hit
                # Same name, different named city: a company's second plant, not this one.
                cand = next(iter(named)) if len(named) == 1 else None
                mine, theirs = addresses.get((fid, h), set()), addresses.get((cand, h0), set())
                same_street = not mine or not theirs or bool({a for a, _ in mine} & {a for a, _ in theirs})
                if cand and (not cities.get((fid, h)) or not cities.get((cand, h0))) and same_street:
                    target, method = cand, "unique_name"
                else:
                    at = one(by_addr, addresses.get((fid, h), ()))
                    if len(at) == 1:
                        target, method = next(iter(at)), "address"
        out.append({"registry_hash": h, "legacy_id": fid, "facility_id": target, "method": method,
                    "confidence": CONFIDENCE[method]})
    return out


PAIRS_SQL = "SELECT DISTINCT facility_key AS facility_id, release_tag FROM fact_assertions"
IDENTITY_SQL = ("SELECT DISTINCT facility_key AS facility_id, release_tag, field_key AS field, value "
                "FROM fact_assertions WHERE field_key IN ('name', 'city', 'state', 'address')")


def crosswalk(wh, dry_run: bool = False) -> dict:
    """Resolve every (registry, number) in the warehouse into legacy_id_map. Idempotent: a re-run
    replaces the map, which is derived and holds no human input. Refuses when the permanent
    registry is empty (seed first) or golden spans more than one release."""
    tags = [r["release_tag"] for r in wh.query("SELECT DISTINCT release_tag FROM golden_facility")]
    if len(tags) != 1:
        raise RuntimeError(f"golden_facility must hold exactly one release, found {len(tags)}")
    permanent = {r["facility_id"] for r in wh.query("SELECT facility_id FROM facility")}
    if not permanent:
        raise RuntimeError("the facility registry is empty: run `python -m pipeline.facility_registry seed` first")
    pairs = [(r["facility_id"], r["release_tag"]) for r in wh.query(PAIRS_SQL)]
    rows = resolve(pairs, wh.query(IDENTITY_SQL), tags[0], permanent)
    releases = sorted({t for _, t in pairs})
    report = {"current_release": tags[0], "registries": len({registry_hash(t) for t in releases}),
              "releases": len(releases), "legacy_pairs": len(rows),
              "by_method": dict(Counter(r["method"] for r in rows).most_common()),
              "permanent_reached": len({r["facility_id"] for r in rows if r["facility_id"]}),
              "permanent_total": len(permanent), "dry_run": dry_run}
    if dry_run:
        return report
    with wh.transaction() as c:
        c.execute("DELETE FROM legacy_id_map")
        c.executemany("INSERT INTO legacy_id_map (registry_hash, legacy_id, facility_id, method, confidence) "
                      "VALUES (?, ?, ?, ?, ?)",
                      [(r["registry_hash"], r["legacy_id"], r["facility_id"], r["method"], r["confidence"]) for r in rows])
        c.execute("DELETE FROM release_registry")
        c.executemany("INSERT INTO release_registry (release_tag, registry_hash) VALUES (?, ?)",
                      [(t, registry_hash(t)) for t in releases])
    return report


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    from .warehouse import open_warehouse, SqliteWarehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.legacy_ids")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crosswalk", help="map every historical IC-number to its permanent facility")
    c.add_argument("--dry-run", action="store_true", help="resolve and report; write nothing")
    args = ap.parse_args(argv)
    wh = (SqliteWarehouse(Path(args.db)) if args.db
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    try:
        print(json.dumps(crosswalk(wh, dry_run=args.dry_run), indent=1))
    except RuntimeError as e:
        print(f"crosswalk refused, nothing written: {e}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
