"""Measure Geocodio's rooftop hit rate on a real sample of our addresses.

A build/don't-build test, not a pipeline stage. It writes nothing into the repo and nothing
into the warehouse — it calls Geocodio on a sample of addresses we already hold and reports
what accuracy came back, so the decision to build the geocode cache rests on our own numbers
rather than a vendor's marketing figure.

    export GEOCODIO_API_KEY=...
    python -m pipeline.geocode_sample --n 200                     # from the warehouse
    python -m pipeline.geocode_sample --n 200 --from-csv rows.csv # from a contract CSV
    python -m pipeline.geocode_sample --n 200 --dry-run           # show the sample, call nothing

Decision rule (docs/geocoding.md): >=90% rooftop, build it; 70-90%, build it and plan a Census
cross-check for the tail; <70%, reconsider the vendor.

Only addresses are sent. No facility name, id, or status leaves the process.
"""
from __future__ import annotations
import argparse, collections, csv, json, os, random, sys, urllib.error, urllib.request
from pathlib import Path

API = "https://api.geocod.io/v2/geocode"
BATCH = 1000           # API allows 10,000; smaller chunks fail cheaply and stay inside the free tier
SAMPLE_SEED = 20260917 # fixed: the same --n always draws the same sample, so runs are comparable


def _from_warehouse() -> list[dict]:
    """Addressed facilities from golden_facility. Requires DATABASE_URL."""
    from .warehouse import open_warehouse
    return open_warehouse().query("""
        SELECT facility_key, address, city, state, zip
        FROM golden_facility
        WHERE address IS NOT NULL AND address <> ''
    """)


def _from_csv(path: Path) -> list[dict]:
    """Contract CSV rows (address_verbatim/city_verbatim/...) or golden-shaped rows."""
    out = []
    with path.open(newline="", encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            get = lambda *ks: next((r[k] for k in ks if r.get(k)), "")
            addr = get("address", "address_verbatim")
            if not addr:
                continue
            out.append({"facility_key": r.get("facility_key") or r.get("row_hash") or str(i),
                        "address": addr,
                        "city": get("city", "city_verbatim"),
                        "state": get("state", "state_verbatim"),
                        "zip": get("zip", "zip_verbatim")})
    return out


def one_line(r: dict) -> str:
    """The single-string form Geocodio's batch endpoint takes."""
    tail = " ".join(p for p in [r.get("city", ""), r.get("state", ""), r.get("zip", "")] if p)
    return f"{r['address']}, {tail}".strip().rstrip(",")


def stratified(rows: list[dict], n: int) -> list[dict]:
    """Sample proportionally by state, so the result reflects the real geographic mix.

    A flat random sample of 200 would over-weight whichever state happens to dominate the
    frame; rooftop coverage is a county-by-county property, so the mix matters.
    """
    if n >= len(rows):
        return list(rows)
    rng = random.Random(SAMPLE_SEED)
    by_state: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        by_state[(r.get("state") or "??").upper()].append(r)
    picked: list[dict] = []
    for st, group in sorted(by_state.items()):
        rng.shuffle(group)
        take = max(1, round(n * len(group) / len(rows)))
        picked.extend(group[:take])
    rng.shuffle(picked)
    return picked[:n]


def geocode(queries: list[str], key: str) -> list[dict]:
    """POST batches to Geocodio. Results come back in input order (documented)."""
    out: list[dict] = []
    for start in range(0, len(queries), BATCH):
        chunk = queries[start:start + BATCH]
        req = urllib.request.Request(
            f"{API}?api_key={key}",
            data=json.dumps(chunk).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise SystemExit(f"Geocodio returned {e.code}: {detail}") from None
        out.extend(body.get("results", []))
        print(f"  ... {min(start + BATCH, len(queries))}/{len(queries)}", file=sys.stderr)
    return out


def summarise(sample: list[dict], results: list[dict]) -> dict:
    """Accuracy mix overall, by state, and by Geocodio's underlying dataset."""
    types, datasets, by_state = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    cross = collections.defaultdict(collections.Counter)   # accuracy_type x underlying dataset family
    scores, misses = [], []
    for row, res in zip(sample, results):
        st = (row.get("state") or "??").upper()
        hits = (res.get("response") or {}).get("results") or []
        if not hits:
            types["no_result"] += 1; by_state[st]["no_result"] += 1
            misses.append(res.get("query", one_line(row)))
            continue
        top = hits[0]
        at = top.get("accuracy_type", "unknown")
        types[at] += 1; by_state[st][at] += 1
        src = top.get("source", "unknown")
        datasets[src] += 1
        cross[at]["TIGER/Line (free Census data)" if "TIGER" in src else "local parcel/address-point file"] += 1
        if isinstance(top.get("accuracy"), (int, float)):
            scores.append(float(top["accuracy"]))
    n = max(1, len(sample))
    return {"n": len(sample), "accuracy_type": dict(types.most_common()),
            "rooftop_pct": round(100 * types["rooftop"] / n, 1),
            "mean_accuracy_score": round(sum(scores) / len(scores), 3) if scores else None,
            "by_state": {s: dict(c.most_common()) for s, c in sorted(by_state.items())},
            "underlying_dataset": dict(datasets.most_common(15)),
            "accuracy_by_dataset_family": {k: dict(v) for k, v in sorted(cross.items())},
            "no_result_examples": misses[:20]}


def verdict(pct: float) -> str:
    if pct >= 90: return "BUILD — rooftop coverage clears the bar."
    if pct >= 70: return "BUILD, with a Census cross-check for the non-rooftop tail."
    return "RECONSIDER — below Geocodio's own random-sample figure; price Smarty before committing."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200, help="sample size (default 200)")
    ap.add_argument("--from-csv", type=Path, help="read addresses from a CSV instead of the warehouse")
    ap.add_argument("--dry-run", action="store_true", help="print the sample; call nothing")
    ap.add_argument("--out", type=Path, help="write the full report JSON here")
    args = ap.parse_args()

    rows = _from_csv(args.from_csv) if args.from_csv else _from_warehouse()
    if not rows:
        raise SystemExit("No addressed rows found. Pass --from-csv, or set DATABASE_URL.")
    sample = stratified(rows, args.n)
    queries = [one_line(r) for r in sample]
    print(f"{len(rows)} addressed rows in frame; sampled {len(sample)} across "
          f"{len({(r.get('state') or '??').upper() for r in sample})} states", file=sys.stderr)

    if args.dry_run:
        for q in queries[:25]:
            print(q)
        if len(queries) > 25:
            print(f"... and {len(queries) - 25} more")
        return 0

    key = os.environ.get("GEOCODIO_API_KEY")
    if not key:
        raise SystemExit("Set GEOCODIO_API_KEY (free key: https://dash.geocod.io/apikey)")

    report = summarise(sample, geocode(queries, key))
    print(json.dumps(report, indent=2))
    print(f"\nrooftop: {report['rooftop_pct']}%  →  {verdict(report['rooftop_pct'])}", file=sys.stderr)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
