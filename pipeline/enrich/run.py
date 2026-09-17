"""The enrichment run: one stage per invocation, so the workflow can make each a separate job.

    python -m pipeline.enrich.run plan      --out enrich   # what each stage would do, no calls
    python -m pipeline.enrich.run locate    --out enrich --limit 200
    python -m pipeline.enrich.run geocode   --out enrich --limit 900
    python -m pipeline.enrich.run footprint --out enrich
    python -m pipeline.enrich.run existence --out enrich
    python -m pipeline.enrich.run load      --out enrich   # append every stage's assertions

Determinism. The sample is seeded, the Overture release is pinned, the model is pinned, and an
assertion's row_hash is taken over the evidence that produced it — so a second run over unchanged
inputs writes the same rows and ON CONFLICT DO NOTHING makes it a no-op. `plan` calls nothing and
is safe to run against production at any time.

Bounded AI. `locate` is the only stage that spends model tokens. It is bounded three ways and
refuses to start if it cannot be: a hard ceiling on facilities per run (--limit, default 200), a
ceiling on searches per facility (the tool's max_uses), and a token ceiling per call. The ceiling
is reported in the metrics whether or not it was reached, so the cost of a run is knowable before
it starts and auditable after.

Observability. Every stage writes <out>/<stage>.json with its counts and its rejections, and
prints a table to stdout and to $GITHUB_STEP_SUMMARY. A stage that does nothing says why.
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

from . import _db

STAGES = ("plan", "locate", "geocode", "footprint", "existence", "load")
DEFAULT_AI_LIMIT = 200
DEFAULT_GEOCODE_LIMIT = 2000        # the free tier is 2500/day and is shared with anyone else using it


def _summary(title: str, rows: list[tuple[str, object]]) -> str:
    body = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return f"\n### {title}\n\n| metric | value |\n| --- | --- |\n{body}\n"


def _emit(out: Path, stage: str, metrics: dict, table: list[tuple[str, object]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stage}.json").write_text(json.dumps(metrics, indent=1, default=str))
    md = _summary(f"Stage: {stage}", table)
    print(md)
    if p := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(p, "a") as fh:
            fh.write(md)


def _load_assertions(out: Path) -> list[dict]:
    got = []
    for stage in ("locate", "geocode", "footprint", "existence"):
        f = out / f"{stage}.assertions.json"
        if f.exists():
            got.extend(json.loads(f.read_text()))
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=STAGES)
    ap.add_argument("--out", default="enrich", type=Path)
    ap.add_argument("--sample", type=int, help="work on a seeded, state-stratified subset")
    ap.add_argument("--limit", type=int, default=DEFAULT_AI_LIMIT,
                    help="hard ceiling on facilities for the AI stage (default 200)")
    ap.add_argument("--geocode-limit", type=int, default=DEFAULT_GEOCODE_LIMIT,
                    help="hard ceiling on Geocodio lookups per run (default 2000, free tier 2500/day)")
    ap.add_argument("--release-tag", default=os.environ.get("ENRICH_RELEASE_TAG", ""))
    ap.add_argument("--dry-run", action="store_true", help="plan the stage; make no external call")
    args = ap.parse_args(argv)

    db = _db.connect()
    rows = _db.snapshot(db, sample=args.sample)
    tag = args.release_tag or (rows[0].get("release_tag") if rows else "") or "enrich"
    def rooftop(r):
        return r.get("has_rooftop") in (True, "t", "true", 1)
    need_addr = _db.needs(rows, "address")
    # an EPA coordinate does not disqualify a facility from being geocoded — it is the reason to
    need_coord = [r for r in rows if (r.get("address") or "").strip() and not rooftop(r)]
    have_coord = [r for r in rows if rooftop(r)]

    if args.stage == "plan":
        _emit(args.out, "plan",
              {"facilities": len(rows), "release_tag": tag, "engine": db.engine,
               "stage9_need_address": len(need_addr), "stage10_need_rooftop": len(need_coord),
               "stage11_have_rooftop": len(have_coord), "ai_limit": args.limit,
               "geocode_limit": args.geocode_limit,
               "coordinates_of_any_provenance": sum(1 for r in rows if (r.get("lat_lon") or "").strip())},
              [("facilities", len(rows)), ("release", tag), ("engine", db.engine),
               ("9 locate — need an address", len(need_addr)),
               ("10 geocode — have address, no rooftop coordinate", len(need_coord)),
               ("11 footprint — have a ROOFTOP coordinate", len(have_coord)),
               ("(coordinates of any provenance, mostly EPA)",
                sum(1 for r in rows if (r.get("lat_lon") or "").strip())),
               ("AI ceiling this run", args.limit),
               ("geocode ceiling this run", args.geocode_limit)])
        return 0

    if args.stage == "locate":
        from . import locate
        todo = need_addr[:args.limit]                      # the bound, applied before any call
        if args.dry_run:
            _emit(args.out, "locate", {"planned": len(todo), "ceiling": args.limit, "called": 0},
                  [("would attempt", len(todo)), ("ceiling", args.limit), ("calls made", 0)])
            return 0
        rep = locate.run(todo)
        (args.out).mkdir(parents=True, exist_ok=True)
        (args.out / "locate.assertions.json").write_text(json.dumps(rep["assertions"], default=str))
        _emit(args.out, "locate",
              {k: v for k, v in rep.items() if k != "assertions"},
              [("eligible", len(need_addr)), ("ceiling", args.limit), ("attempted", rep["requested"]),
               ("located with a citation", rep["located"]),
               ("rejected", len(rep["rejected"])), ("model", rep["model"])])
        return 0

    if args.stage == "geocode":
        from . import geocode
        todo = need_coord[:args.geocode_limit]     # the free tier is shared; never spend it all
        if args.dry_run:
            _emit(args.out, "geocode", {"planned": len(todo), "called": 0},
                  [("would look up", len(todo)), ("calls made", 0)])
            return 0
        rep = geocode.run(todo)
        (args.out).mkdir(parents=True, exist_ok=True)
        (args.out / "geocode.assertions.json").write_text(json.dumps(rep["assertions"], default=str))
        _emit(args.out, "geocode", {k: v for k, v in rep.items() if k != "assertions"},
              [("eligible", len(need_coord)), ("ceiling", args.geocode_limit),
               ("looked up", rep["requested"]), ("rooftop stored", rep["stored"]),
               ("rooftop %", rep["rooftop_pct"]),
               ("not stored (non-rooftop)", rep["requested"] - rep["stored"]),
               ("accuracy mix", json.dumps(rep["accuracy_type"]))])
        return 0

    if args.stage == "footprint":
        from . import footprint
        from ._db import assertion
        # A rooftop coordinate counts whether it is already in the database or stage 10 produced it
        # a moment ago in this same run. `load` deliberately runs last so the write is atomic, which
        # means stage 10's coordinates are not in the database yet when stage 11 runs — reading only
        # the database would make every first run measure nothing.
        coords: dict[str, str] = {r["facility_id"]: r["lat_lon"] for r in have_coord}
        fresh = args.out / "geocode.assertions.json"
        if fresh.exists():
            for a in json.loads(fresh.read_text()):
                if a.get("field") == "lat_lon" and a.get("basis") == "rooftop":
                    coords[a["facility_id"]] = a["value"]
        pts = []
        for fid, latlon in coords.items():
            try:
                la, lo = [float(x) for x in (latlon or "").split(",")[:2]]
            except ValueError:
                continue
            if -90 <= la <= 90 and -180 <= lo <= 180:
                pts.append({"facility_id": fid, "lat": la, "lon": lo})
        if args.dry_run:
            _emit(args.out, "footprint", {"planned": len(pts), "called": 0},
                  [("would measure", len(pts)), ("calls made", 0)])
            return 0
        res = footprint.measure(pts, cache=args.out / "overture_index.json")
        got = [r for r in res if r.get("building_sqft")]
        asserts = [assertion(r["facility_id"], "building_sqft", str(r["building_sqft"]),
                             source_id="overture:building", basis="footprint",
                             evidence=f"{r['overture_release']}:{r['building_id']}@{r['offset_m']}m")
                   for r in got]
        (args.out).mkdir(parents=True, exist_ok=True)
        (args.out / "footprint.assertions.json").write_text(json.dumps(asserts, default=str))
        (args.out / "footprint.rows.json").write_text(json.dumps(res, default=str))
        sq = sorted(r["building_sqft"] for r in got)
        _emit(args.out, "footprint",
              {"measured": len(got), "attempted": len(pts),
               "median_sqft": sq[len(sq) // 2] if sq else None,
               "under_10k": sum(1 for x in sq if x < 10000),
               "release": footprint.DEFAULT_RELEASE},
              [("coordinates", len(pts)), ("measured", len(got)),
               ("no building within 30m", len(pts) - len(got)),
               ("median sqft", f"{sq[len(sq)//2]:,}" if sq else "-"),
               ("under 10k sqft (flagged)", sum(1 for x in sq if x < 10000)),
               ("overture release", footprint.DEFAULT_RELEASE)])
        return 0

    if args.stage == "existence":
        from . import existence
        rep = existence.run(rows, args.out)
        (args.out / "existence.assertions.json").write_text(json.dumps(rep["assertions"], default=str))
        _emit(args.out, "existence", {k: v for k, v in rep.items() if k != "assertions"},
              [("examined", rep["examined"]), ("flagged", rep["flagged"]),
               ("reasons", json.dumps(rep["reasons"]))])
        return 0

    if args.stage == "load":
        from . import gates
        asserts = _load_assertions(args.out)
        # Gates run before the write, not after it: a gate that reports on a release it has already
        # published is a report, not a gate.
        results = gates.run_all(asserts, before=rows, after=rows)
        for r in results:
            print(f"  {r}")
        failed = [r for r in results if not r.passed]
        if failed:
            _emit(args.out, "load",
                  {"halted": True, "gates": [str(r) for r in results], "appended": 0},
                  [("HALTED", "a gate failed"), *[(r.gate, r.summary) for r in results]])
            print(f"\nHALT: {len(failed)} gate(s) failed; nothing was written", file=sys.stderr)
            return 1
        if args.dry_run:
            _emit(args.out, "load", {"would_append": len(asserts), "gates": [str(r) for r in results]},
                  [("would append", len(asserts)), *[(r.gate, r.summary) for r in results]])
            return 0
        wrote = _db.append(db, asserts, tag)
        by_field: dict[str, int] = {}
        for a in asserts:
            by_field[a["field"]] = by_field.get(a["field"], 0) + 1
        _emit(args.out, "load",
              {**wrote, "release_tag": tag, "by_field": by_field,
               "gates": [str(r) for r in results]},
              [("assertions offered", wrote["offered"]),
               ("newly inserted", wrote["inserted"]),
               ("already present (re-run is a no-op)", wrote["already_present"]),
               ("release tag", tag), ("by field", json.dumps(by_field)),
               *[(r.gate, r.summary) for r in results]])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
