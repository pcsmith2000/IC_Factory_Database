"""Duplicate facilities: two live IC-numbers at one parcel, proposed, tiered, and merged (#52).

The permanent registry (pipeline/facility_registry.py) never merges on its own: a false merge
loses a plant, a missed one only shows it twice. On 2026-09-26 golden still held ~360 groups (584
pairs) of live numbers at the same parcel, e.g. Cavco at 2502 W Durango St, Phoenix, under
IC-09911, IC-93656 and IC-95267. Some groups are one plant spelled three ways; some are a
successor company or a campus with two firms. So a merge is proposed first, in
facility_duplicate_candidate, with a tier saying how sure the proposal is:

    certain   same parcel AND the names agree once normalised ("Cavco Industries, Inc." is
              "Cavco"), or one name's words are a subset of the other's with a distinctive word
              ("Madison" in "Madison Industries of Georgia"), or the same phone or website domain
    likely    same parcel and a shared distinctive word ("Champion Home Builders" / "Champion
              Homes of Sangerfield")
    review    same parcel, names unrelated: possibly a different operator; a person decides

Same parcel = same state, same city, same house number, and street_equivalent streets
(pipeline/recovery/streets.py), without the ZIP relaxation: "Alamo Dr" is not "Alamo Rd" here.

A group of same-parcel numbers collapses to one survivor: the one with the most assertions, then
the lowest number. Every other member gets a candidate row pointing at it, tiered by the
strongest chain of matches that reaches it. A member that only reaches the survivor at `review`
but is `certain` with a neighbour also gets a row pointing at that neighbour's cluster survivor,
so `apply --tier certain` still folds the certain part of a mixed group together.

    python -m pipeline.duplicates candidates [--dry-run]
    python -m pipeline.duplicates apply [--tier certain --tier likely] --actor NAME [--dry-run] [--limit N]
    python -m pipeline.duplicates decide IC-x --of IC-y --status rejected --actor NAME [--reason "..."]
"""
from __future__ import annotations
import json, re, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
TIERS = ("certain", "likely", "review")          # strongest first
RANK = {t: i for i, t in enumerate(TIERS)}

LEGAL = {"inc", "llc", "ltd", "corp", "co", "company", "incorporated", "corporation", "lp", "llp",
         "pllc", "plc", "pc", "limited", "the"}
# Trailing words that name a site of a firm, not the firm: "Clayton Homes - Shelby", "Plant #2".
SITE_WORDS = {"plant", "division", "div", "facility", "location", "site", "no", "number", "branch",
              "yard", "shop", "office", "factory", "campus", "unit"}
STOP = {"of", "and", "a", "at", "by", "for", "in", "on", "de"}
# Words many unrelated plants share: sharing one of these says nothing about being one firm.
GENERIC = {"home", "homes", "industries", "industry", "industrial", "building", "buildings", "builder",
           "builders", "system", "systems", "manufacturing", "mfg", "manufactured", "manufacturer",
           "modular", "construction", "constructors", "contractors", "structures", "structural",
           "steel", "truss", "trusses", "group", "enterprises", "enterprise", "products", "product",
           "precast", "concrete", "components", "component", "housing", "solutions", "services",
           "international", "america", "american", "usa", "us", "national", "north", "south", "east",
           "west", "supply", "wood", "lumber", "holdings", "mobile", "custom", "design", "fabrication",
           "fabricators", "metal", "metals", "panel", "panels", "prefab", "framing", "wall", "walls",
           "roof", "roofing", "cabinet", "cabinets", "millwork", "company", "corp", "technologies",
           "technology", "works", "mills", "mill", "sales", "new"}
# Hosts that are not a firm's own domain: a shared Facebook page names nobody.
SHARED_HOSTS = {"facebook.com", "google.com", "sites.google.com", "linkedin.com", "yelp.com",
                "business.site", "wixsite.com", "godaddysites.com", "instagram.com", "twitter.com",
                "x.com", "mapquest.com", "bbb.org", "yellowpages.com"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- normalisation
def name_variants(name) -> set[str]:
    """Normalised spellings of one name: the whole, and each side of a d/b/a."""
    s = str(name or "").lower().replace("&", " and ")
    parts = re.split(r"\b(?:d\s*/\s*b\s*/\s*a|dba|d\.b\.a\.?|a\s*/\s*k\s*/\s*a|aka|formerly)\b", s)
    out = set()
    for p in parts:
        p = re.sub(r"\([^)]*\)", " ", p)                    # "(Shelby)", "(formerly X)"
        p = re.sub(r"\s[-–—|]\s.*$", " ", p)                 # "Clayton Homes - Shelby"
        p = re.sub(r"#\s*\d+\w*", " ", p)                   # "Plant #2"
        toks = [t for t in re.split(r"[^a-z0-9]+", p) if t]
        toks = [t for t in toks if t not in LEGAL and t not in ("dba", "aka")]
        while toks and (toks[-1] in SITE_WORDS or re.fullmatch(r"\d+", toks[-1])):
            toks.pop()                                        # trailing "plant 2", "division"
        if toks:
            out.add(" ".join(toks))
    return out


def _tokens(variant: str) -> set[str]:
    return {t for t in variant.split() if t not in STOP}


def _distinctive(tokens: set[str]) -> set[str]:
    return {t for t in tokens if t not in GENERIC and len(t) > 1 and not t.isdigit()}


def phone10(v) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    d = d[1:] if len(d) == 11 and d.startswith("1") else d
    return d if len(d) == 10 else ""


def domain(v) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return ""
    host = (urlparse(s if "://" in s else "http://" + s).hostname or "").removeprefix("www.")
    if not host or "." not in host or any(host == h or host.endswith("." + h) for h in SHARED_HOSTS):
        return ""
    return host


def _city(v) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", str(v or "").lower())
    s = re.sub(r"\bsaint\b", "st", re.sub(r"\bfort\b", "ft", re.sub(r"\bmount\b", "mt", s)))
    return s.replace(" ", "")


def _street(address) -> str:
    return str(address or "").split(",")[0].strip()


def house_number(address) -> str:
    m = re.match(r"\s*(\d+)\b", _street(address))
    return m.group(1) if m else ""


def same_parcel(a: dict, b: dict) -> tuple[bool, str]:
    from .recovery.streets import street_equivalent
    if (str(a.get("state") or "").upper() != str(b.get("state") or "").upper() or not a.get("state")
            or _city(a.get("city")) != _city(b.get("city")) or not _city(a.get("city"))
            or not house_number(a.get("address")) or house_number(a.get("address")) != house_number(b.get("address"))):
        return False, "different_place"
    return street_equivalent(_street(a.get("address")), _street(b.get("address")))


def name_tier(a: dict, b: dict) -> tuple[str, str]:
    """(tier, reason) for two rows already known to share a parcel."""
    va = set().union(*(name_variants(a.get(f)) for f in ("name", "legal_name")))
    vb = set().union(*(name_variants(b.get(f)) for f in ("name", "legal_name")))
    if va & vb:
        return "certain", "same_name"
    for x in va:
        for y in vb:
            tx, ty = _tokens(x), _tokens(y)
            small = tx if len(tx) <= len(ty) else ty
            if tx and ty and (tx <= ty or ty <= tx) and _distinctive(small):
                return "certain", "name_subset"
    if phone10(a.get("phone")) and phone10(a.get("phone")) == phone10(b.get("phone")):
        return "certain", "same_phone"
    if domain(a.get("website")) and domain(a.get("website")) == domain(b.get("website")):
        return "certain", "same_website"
    da = set().union(*(_distinctive(_tokens(v)) for v in va)) if va else set()
    db = set().union(*(_distinctive(_tokens(v)) for v in vb)) if vb else set()
    if da & db:
        return "likely", "shared_word:" + ",".join(sorted(da & db))
    return "review", "names_unrelated"


def _id_rank(fid: str) -> tuple:
    from .facility_registry import id_number
    n = id_number(fid)
    return (n is None, n if n is not None else 0, fid)


def _survivor(members: list[dict]) -> dict:
    return min(members, key=lambda r: (-int(r.get("n_assertions") or 0), _id_rank(r["facility_key"])))


def _components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return list(groups.values())


def scan(rows: list[dict]) -> list[dict]:
    """Candidate rows over golden rows. Pure.

    Rows are bucketed by (state, city, house number), paired within a bucket by street_equivalent,
    and joined transitively into parcel groups. Each pair is tiered by its names; for each tier
    the pairs at least that strong form clusters, and every member of a cluster that is not its
    survivor gets a row at that tier pointing at the cluster's survivor. A member's strongest row
    wins when two levels name the same survivor."""
    buckets: dict[tuple, list[dict]] = {}
    for r in rows:
        hn = house_number(r.get("address"))
        if hn and r.get("state") and _city(r.get("city")):
            buckets.setdefault((str(r["state"]).upper(), _city(r.get("city")), hn), []).append(r)
    out = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        by_id = {r["facility_key"]: r for r in bucket}
        pairs = {}                                           # (a, b) -> (tier, reason, street rule)
        for i, a in enumerate(bucket):
            for b in bucket[i + 1:]:
                ok, rule = same_parcel(a, b)
                if ok:
                    pairs[(a["facility_key"], b["facility_key"])] = (*name_tier(a, b), rule)
        if not pairs:
            continue
        ids = sorted({f for p in pairs for f in p})
        rows_out: dict[tuple[str, str], dict] = {}
        for level in reversed(TIERS):                        # review (whole group) first, certain last
            edges = [p for p, (t, _, _) in pairs.items() if RANK[t] <= RANK[level]]
            for comp in _components(ids, edges):
                if len(comp) < 2:
                    continue
                surv = _survivor([by_id[f] for f in comp])["facility_key"]
                for f in comp:
                    if f != surv:
                        rows_out[(f, surv)] = _candidate(f, surv, level, pairs, comp, by_id)
        out.extend(rows_out.values())
    return sorted(out, key=lambda c: (RANK[c["tier"]], c["duplicate_of"], c["facility_id"]))


def _candidate(f: str, surv: str, tier: str, pairs: dict, comp: list[str], by_id: dict) -> dict:
    direct = pairs.get((f, surv)) or pairs.get((surv, f))
    reason = direct[1] if direct and RANK[direct[0]] <= RANK[tier] else "transitive"
    ev = {"reason": reason, "street_rule": direct[2] if direct else None,
          "group": sorted(comp),
          "names": {i: by_id[i].get("name") for i in (f, surv)},
          "legal_names": {i: by_id[i].get("legal_name") for i in (f, surv) if by_id[i].get("legal_name")},
          "addresses": {i: f'{by_id[i].get("address")}, {by_id[i].get("city")}, {by_id[i].get("state")}' for i in (f, surv)},
          "n_assertions": {i: by_id[i].get("n_assertions") for i in (f, surv)}}
    return {"facility_id": f, "duplicate_of": surv, "tier": tier, "evidence": ev}


# ---------------------------------------------------------------- warehouse
GOLDEN_SQL = ("SELECT g.facility_key, g.name, g.legal_name, g.address, g.city, g.state, g.website, g.phone, "
              "g.n_assertions, g.capability_group FROM golden_facility g "
              "JOIN facility f ON f.facility_id = g.facility_key WHERE f.status = 'active'")


def candidates(wh, dry_run: bool = False) -> dict:
    """Scan active golden and record what it finds as source 'parcel_scan'. Its own pending rows
    are re-tiered; a row from research or an operator is left as it is; merged and rejected rows
    are decisions and are never touched, so a rejected pair is not proposed again. A pending parcel_scan row the scan no longer finds is removed: it was
    derived, and a plant that moved or left golden is no longer a candidate."""
    rows = wh.query(GOLDEN_SQL)
    if not rows:
        raise RuntimeError("no active golden facility: seed the registry first (python -m pipeline.facility_registry seed)")
    found = scan(rows)
    existing = {(r["facility_id"], r["duplicate_of"]): r for r in
                wh.query("SELECT facility_id, duplicate_of, source, tier, evidence, status FROM facility_duplicate_candidate")}
    new, retiered, unchanged, decided = [], [], 0, Counter()
    for c in found:
        e = existing.get((c["facility_id"], c["duplicate_of"]))
        if e is None:
            new.append(c)
        elif e["status"] != "pending":
            decided[e["status"]] += 1
        elif e["source"] != "parcel_scan":
            decided["pending_from_" + e["source"]] += 1          # research's or a person's row: left as it is
        elif e["tier"] != c["tier"] or e["evidence"] != json.dumps(c["evidence"], sort_keys=True):
            retiered.append(c)
        else:
            unchanged += 1
    keys = {(c["facility_id"], c["duplicate_of"]) for c in found}
    stale = [k for k, e in existing.items() if e["source"] == "parcel_scan" and e["status"] == "pending" and k not in keys]
    by_tier = Counter(c["tier"] for c in found)
    report = {"golden_active": len(rows), "candidates": len(found),
              "by_tier": {t: by_tier.get(t, 0) for t in TIERS},
              "groups": len({c["duplicate_of"] for c in found}),
              "new": len(new), "updated": len(retiered), "unchanged": unchanged,
              "already_decided": dict(decided), "stale_removed": len(stale), "dry_run": dry_run,
              "examples": {t: [_example(c) for c in found if c["tier"] == t][:5] for t in TIERS}}
    if dry_run:
        return report
    at = _now()
    with wh.transaction() as c:
        c.executemany("INSERT INTO facility_duplicate_candidate (facility_id, duplicate_of, source, tier, evidence, "
                      "status, created_at) VALUES (?, ?, 'parcel_scan', ?, ?, 'pending', ?) "
                      "ON CONFLICT (facility_id, duplicate_of) DO UPDATE SET tier = excluded.tier, "
                      "evidence = excluded.evidence WHERE facility_duplicate_candidate.status = 'pending' "
                      "AND facility_duplicate_candidate.source = 'parcel_scan'",
                      [(x["facility_id"], x["duplicate_of"], x["tier"], json.dumps(x["evidence"], sort_keys=True), at)
                       for x in new + retiered])
        c.executemany("DELETE FROM facility_duplicate_candidate WHERE facility_id = ? AND duplicate_of = ? "
                      "AND status = 'pending' AND source = 'parcel_scan'", stale)
    return report


def _example(c: dict) -> str:
    ev = c["evidence"]
    return (f'{c["facility_id"]} "{ev["names"][c["facility_id"]]}" -> {c["duplicate_of"]} '
            f'"{ev["names"][c["duplicate_of"]]}" @ {ev["addresses"][c["duplicate_of"]]} ({ev["reason"]})')


def _registry_state(wh) -> dict[str, tuple[str, str | None]]:
    return {r["facility_id"]: (r["status"], r["merged_into"])
            for r in wh.query("SELECT facility_id, status, merged_into FROM facility")}


def _mark(wh, facility_id: str, duplicate_of: str, status: str, actor: str):
    with wh.transaction() as c:
        c.execute("UPDATE facility_duplicate_candidate SET status = ?, decided_at = ?, decided_by = ? "
                  "WHERE facility_id = ? AND duplicate_of = ?", (status, _now(), actor, facility_id, duplicate_of))


def apply(wh, tiers=("certain",), actor: str = "", dry_run: bool = True, limit: int | None = None,
          sources: tuple[str, ...] = (), only: tuple[str, ...] = (), skip: tuple[str, ...] = ()) -> dict:
    """Merge pending candidates of the given tiers through facility_registry.merge, strongest
    tier first. A pair whose source is already merged into its target (an earlier run died between
    the merge and the mark, or a person merged it by hand) is marked merged; a pair either side of
    which is no longer active is skipped and reported, and stays pending. Idempotent. A dry run
    simulates the merges in memory, so its skips are the ones a real run would make.

    `sources` limits it to candidates one source proposed (e.g. web_research, reviewed apart from
    the parcel scan's pairs of the same tier); `only` to these facility ids (a reviewed list);
    `skip` leaves these facility ids pending for a person."""
    from .facility_registry import merge
    if not actor:
        raise ValueError("apply needs an actor")
    bad = [t for t in tiers if t not in RANK]
    if bad:
        raise ValueError(f"unknown tier(s) {bad}; expected {TIERS}")
    ph = ",".join("?" * len(tiers))
    todo = wh.query(f"SELECT facility_id, duplicate_of, tier, source, evidence FROM facility_duplicate_candidate "
                    f"WHERE status = 'pending' AND tier IN ({ph})", tuple(tiers))
    todo = [r for r in todo if (not sources or r["source"] in sources)
            and (not only or r["facility_id"] in only) and r["facility_id"] not in skip]
    todo.sort(key=lambda r: (RANK[r["tier"]], r["duplicate_of"], r["facility_id"]))
    state = _registry_state(wh)
    merged, skipped, marked = [], [], []
    for r in todo:
        if limit is not None and len(merged) >= limit:
            break
        f, into = r["facility_id"], r["duplicate_of"]
        sf, si = state.get(f, ("missing", None)), state.get(into, ("missing", None))
        if sf == ("merged", into):
            marked.append(f"{f} -> {into}")
            if not dry_run:
                _mark(wh, f, into, "merged", actor)
            continue
        if sf[0] != "active" or si[0] != "active":
            skipped.append({"facility_id": f, "duplicate_of": into, "tier": r["tier"],
                            "why": f"{f} is {sf[0]}" if sf[0] != "active" else f"{into} is {si[0]}"})
            continue
        if not dry_run:
            names = (json.loads(r["evidence"] or "{}").get("names") or {})
            merge(wh, f, into, actor, f'duplicate: {r["tier"]} same parcel ({r["source"]}) '
                                      f'"{names.get(f, "")}" = "{names.get(into, "")}"')
            _mark(wh, f, into, "merged", actor)
        # the registry re-roots anything merged into f; mirror it so later rows see what a run would
        state = {k: (("merged", into) if v == ("merged", f) else v) for k, v in state.items()}
        state[f] = ("merged", into)
        merged.append({"facility_id": f, "into": into, "tier": r["tier"]})
    return {"tiers": list(tiers), "sources": list(sources), "only": len(only), "skip": list(skip),
            "pending_in_tiers": len(todo), "merged": len(merged),
            "marked_already_merged": len(marked), "skipped": len(skipped), "dry_run": dry_run,
            "merges": merged[:50], "skips": skipped[:50]}


def decide(wh, facility_id: str, duplicate_of: str, status: str, actor: str, reason: str = "") -> dict:
    """A person's ruling on a pair. 'rejected': never merged, never proposed again. 'merged': merge
    through the registry now (unless already done). A pair no scan proposed is recorded as source
    'operator', so a rejection can be made before any scan finds it."""
    from .facility_registry import merge
    if status not in ("merged", "rejected", "pending"):
        raise ValueError(f"status must be merged | rejected | pending, not {status!r}")
    if facility_id == duplicate_of:
        raise ValueError("a facility is not a duplicate of itself")
    if status == "merged" and _registry_state(wh).get(facility_id) != ("merged", duplicate_of):
        merge(wh, facility_id, duplicate_of, actor, f"duplicate: operator decision{': ' + reason if reason else ''}")
    with wh.transaction() as c:
        c.execute("INSERT INTO facility_duplicate_candidate (facility_id, duplicate_of, source, tier, evidence, status, created_at) "
                  "VALUES (?, ?, 'operator', 'review', ?, 'pending', ?) ON CONFLICT (facility_id, duplicate_of) DO NOTHING",
                  (facility_id, duplicate_of, json.dumps({"reason": reason or "operator decision"}), _now()))
    if status == "pending":
        with wh.transaction() as c:
            c.execute("UPDATE facility_duplicate_candidate SET status = 'pending', decided_at = NULL, decided_by = NULL "
                      "WHERE facility_id = ? AND duplicate_of = ?", (facility_id, duplicate_of))
    else:
        _mark(wh, facility_id, duplicate_of, status, actor)
    return wh.query("SELECT facility_id, duplicate_of, source, tier, status, decided_at, decided_by "
                    "FROM facility_duplicate_candidate WHERE facility_id = ? AND duplicate_of = ?",
                    (facility_id, duplicate_of))[0]


def main(argv=None) -> int:
    import argparse
    from .facility_registry import RegistryConflict
    from .registry import load_yaml
    from .warehouse import open_warehouse, SqliteWarehouse
    db = argparse.ArgumentParser(add_help=False)
    db.add_argument("--db", default=argparse.SUPPRESS, help="sqlite path; default: the configured engine")
    ap = argparse.ArgumentParser(prog="python -m pipeline.duplicates", parents=[db])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates", parents=[db], help="scan active golden for same-parcel numbers")
    c.add_argument("--dry-run", action="store_true", help="scan and report; write nothing")
    a = sub.add_parser("apply", parents=[db], help="merge pending candidates of the given tiers")
    a.add_argument("--tier", action="append", choices=TIERS, help="repeatable; default: certain")
    a.add_argument("--actor", required=True, help="who: a person or a named agent")
    a.add_argument("--dry-run", action="store_true", help="report what would merge; write nothing")
    a.add_argument("--limit", type=int, default=None, help="merge at most N")
    a.add_argument("--source", action="append", default=[], help="repeatable: only candidates this source proposed")
    a.add_argument("--only", default="", help="comma-separated facility ids: merge only these (a reviewed list)")
    a.add_argument("--skip", default="", help="comma-separated facility ids to leave pending for a person")
    d = sub.add_parser("decide", parents=[db], help="record a person's ruling on one pair")
    d.add_argument("target")
    d.add_argument("--of", required=True, dest="duplicate_of")
    d.add_argument("--status", required=True, choices=("rejected", "merged", "pending"))
    d.add_argument("--actor", required=True)
    d.add_argument("--reason", default="")
    args = ap.parse_args(argv)
    path = getattr(args, "db", None)
    wh = (SqliteWarehouse(Path(path)) if path
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    try:
        if args.cmd == "candidates":
            out = candidates(wh, dry_run=args.dry_run)
        elif args.cmd == "apply":
            ids = lambda v: tuple(x.strip() for x in v.split(",") if x.strip())
            out = apply(wh, tuple(args.tier or ("certain",)), args.actor, dry_run=args.dry_run, limit=args.limit,
                        sources=tuple(args.source), only=ids(args.only), skip=ids(args.skip))
        else:
            out = decide(wh, args.target, args.duplicate_of, args.status, args.actor, args.reason)
    except (RuntimeError, ValueError, RegistryConflict) as e:
        print(f"refused, nothing written: {e}", file=sys.stderr); return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
