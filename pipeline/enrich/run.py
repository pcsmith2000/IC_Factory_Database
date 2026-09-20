"""The enrichment run: one stage per invocation, so the workflow can make each a separate job.

    python -m pipeline.enrich.run plan      --out enrich   # what each stage would do, no calls
    python -m pipeline.enrich.run locate    --out enrich --limit 200 [--model <id>]
    python -m pipeline.enrich.run geocode   --out enrich --limit 900
    python -m pipeline.enrich.run footprint --out enrich
    python -m pipeline.enrich.run existence --out enrich
    python -m pipeline.enrich.run load      --out enrich   # append every stage's assertions
    python -m pipeline.enrich.run promote   --out enrich   # survivorship again, so golden shows it
    python -m pipeline.enrich.run cache     --out enrich --cache-dump ledger.json

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
import argparse, collections, json, os, sys
from pathlib import Path

from . import _db

STAGES = ("plan", "locate", "geocode", "places", "footprint", "existence", "load", "promote",
          "cache")
DEFAULT_AI_LIMIT = 200
DEFAULT_GEOCODE_LIMIT = 2000        # the free tier is 2500/day and is shared with anyone else using it
# Footprint cost is per distinct Overture file, not per facility: ten plants in one county read one
# file, ten spread across ten states read ten. A nationally spread run therefore touches a large
# share of the 512 files at roughly a minute each, which overruns the job long before it runs out
# of facilities. The ceiling is on files for that reason, and the remainder is left for the next
# run — every coordinate is still measured eventually, and no run is open-ended.
# Sized against the 35 minute stage timeout, not against appetite: a file takes roughly a minute,
# so a ceiling of 40 could never be reached before the timeout killed the job — the timeout would
# become the real bound and the deferral path, which is what lets the next run continue cleanly,
# would never run. 20 leaves headroom for the slowest files and for stage startup.
DEFAULT_FOOTPRINT_LIMIT = 20
# Places cost is per STATE box read from S3, the same shape as footprint's per-file cost: the
# facilities in a state are answered by one read however many there are. Six states is roughly
# ten minutes against the 35 minute stage timeout, and the rest defer to the next run.
DEFAULT_PLACES_STATES = 6


def _summary(title: str, rows: list[tuple[str, object]]) -> str:
    body = "\n".join(f"| {k} | {v} |" for k, v in rows)
    return f"\n### {title}\n\n| metric | value |\n| --- | --- |\n{body}\n"


def _emit(out: Path, stage: str, metrics: dict, table: list[tuple[str, object]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stage}.json").write_text(json.dumps(metrics, indent=1, default=str))
    from ..run_report import emit
    emit(stage, "failure" if metrics.get("halted") else "success", metrics)
    md = _summary(f"Stage: {stage}", table)
    print(md)
    if p := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(p, "a") as fh:
            fh.write(md)


def _load_assertions(out: Path) -> list[dict]:
    got = []
    for stage in ("locate", "geocode", "places", "footprint", "existence"):
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
    ap.add_argument("--regeocode", action="store_true",
                    help="look up addresses again that a previous run could not place")
    ap.add_argument("--places-limit", type=int, default=DEFAULT_PLACES_STATES,
                    help="hard ceiling on STATES whose Overture places a run may read (default 6); "
                         "cost is per state box read, not per facility")
    ap.add_argument("--footprint-limit", type=int, default=DEFAULT_FOOTPRINT_LIMIT,
                    help="hard ceiling on distinct Overture files a run may read (default 40)")
    ap.add_argument("--model", default="", help="model id for the AI stage (default: see locate.DEFAULT_MODEL)")
    ap.add_argument("--search", default="", help="search provider: parallel | perplexity | exa | tako | native")
    # 1800 against a 2100s job timeout. Measured on run 33: job setup and the pip install take ~35s
    # and the artifact upload ~1s, so 300s of slack is ample, and every 60s of deadline is another
    # four facilities at the measured 13.8s each.
    ap.add_argument("--deadline", type=float, default=float(os.environ.get("ENRICH_DEADLINE_S", 1800)),
                    help="seconds the AI stage may spend before deferring the rest (default 1800, "
                         "against a 35 minute job timeout)")
    ap.add_argument("--cache-dump", default="", help="cache stage: write the ledger to this file")
    ap.add_argument("--cache-restore", default="", help="cache stage: load a ledger from this file")
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
    tried = lambda r: r.get("geocode_tried") in (True, "t", "true", 1)
    # an address already looked up and found unplaceable is not re-looked-up, unless asked
    need_coord = [r for r in rows if (r.get("address") or "").strip() and not rooftop(r)
                  and (args.regeocode or not tried(r))]
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
        # Every eligible facility, not a slice: the ceiling is enforced on model calls inside
        # locate.run, so a facility the ledger already answered can be skipped without spending
        # one. Truncating here instead let cached rows eat the budget and stalled the backlog.
        todo = need_addr
        if args.dry_run:
            # An upper bound, not a count: how many of these the ledger can answer for free is only
            # known once it is read, so the real number of calls is at most the ceiling.
            planned = min(len(todo), args.limit)
            _emit(args.out, "locate", {"planned": planned, "eligible": len(todo),
                                       "ceiling": args.limit, "called": 0},
                  [("eligible", len(todo)), ("would attempt at most", planned),
                   ("ceiling (model calls)", args.limit), ("calls made", 0)])
            return 0
        kw = {}
        if args.model:
            kw["model"] = args.model
        if args.search:
            kw["search"] = args.search
        (args.out).mkdir(parents=True, exist_ok=True)

        def _save(rep):
            """Written as the stage goes, not only when it ends.

            The artifact upload runs `if: always()`, so a stage killed by the job timeout would
            still upload — but there was nothing on disk to upload, because the files were written
            after the loop. Everything a long run had found was lost with it.
            """
            from ..run_report import emit
            emit("locate", "running", rep)
            (args.out / "locate.assertions.json").write_text(json.dumps(rep["assertions"], default=str))
            (args.out / "locate.json").write_text(json.dumps(
                {k: v for k, v in rep.items() if k != "assertions"}, indent=1, default=str))

        rep = locate.run(todo, deadline_s=args.deadline, checkpoint=_save, db=db,
                         limit=args.limit, **kw)
        _save(rep)
        table = [("eligible", len(need_addr)), ("ceiling (model calls)", args.limit),
                 ("considered", rep["requested"]),
                 ("attempted (reached the model)", rep["attempted"]),
                 ("served from the lookup ledger (no model call)", rep.get("from_cache", 0)),
                 ("located with a citation", rep["located"]),
                 ("yield %", rep["yield_pct"]),
                 ("rejected", len(rep["rejected"])),
                 ("why rejected", json.dumps(rep["rejected_by_reason"])),
                 ("usage", json.dumps(rep["usage"])),
                 ("usage per located address", json.dumps(rep["usage_per_located"])),
                 ("model", rep["model"]), ("search", rep["search"])]
        if rep.get("stopped_early"):
            # Say which of the three actually stopped it. This warning used to read "AI Gateway key
            # budget exhausted" whatever the cause, so a run that simply ran out of clock reported
            # a spent budget — and was believed.
            why = rep.get("stop_reason", "")
            label = ("DEFERRED (stage deadline)" if "deadline" in why else
                     "DEFERRED (run ceiling)" if "ceiling" in why else
                     "DEFERRED (AI Gateway budget spent)")
            table.insert(0, (label, rep["deferred"]))
            print(f"::warning::stage 9 stopped early after {rep['attempted']} model calls; "
                  f"{rep['deferred']} facilities deferred to the next run. {why}")
        _emit(args.out, "locate", {k: v for k, v in rep.items() if k != "assertions"}, table)
        return 0

    if args.stage == "geocode":
        from . import geocode
        # The spend dial. 2,500 lookups a day are free and the rest bills at $1/1000, so a ceiling
        # per run is only half the story — a loop of nine runs a day at 2,000 each would bill for
        # 15,500 of them. Set this against the day's runs, not against one.
        todo = need_coord[:args.geocode_limit]
        if args.dry_run:
            _emit(args.out, "geocode", {"planned": len(todo), "called": 0},
                  [("would look up", len(todo)), ("calls made", 0)])
            return 0
        rep = geocode.run(todo, db=db)
        (args.out).mkdir(parents=True, exist_ok=True)
        (args.out / "geocode.assertions.json").write_text(json.dumps(rep["assertions"], default=str))
        table = [("eligible", len(need_coord)), ("ceiling", args.geocode_limit),
                 ("served from the lookup ledger (not billed)", rep.get("from_cache", 0)),
                 ("looked up", rep["requested"]), ("rooftop coordinates stored", rep["stored"]),
                 ("recorded unplaceable (not retried next run)", rep["quality_flags_recorded"]),
                 ("rooftop %", rep["rooftop_pct"]),
                 ("not stored (non-rooftop)", rep["requested"] - rep["stored"]),
                 ("accuracy mix", json.dumps(rep["accuracy_type"])),
               ("billable if the day's 2,500 free are already spent",
                f"${rep.get('billable_if_allowance_spent_usd', 0):.3f}")]
        if rep.get("quota_exhausted"):
            # Loud, and green. The stage did what it could and the rest is deferred, not lost —
            # but a run that says nothing would leave the ceiling invisible until someone wondered
            # why the coordinate count stopped moving.
            table.insert(0, ("DEFERRED (Geocodio free tier spent)", rep["deferred"]))
            print(f"::warning::Geocodio free tier exhausted after {rep['requested']} of "
                  f"{rep['selected']} lookups; {rep['deferred']} deferred to the next run. "
                  f"Add a payment method at https://dash.geocod.io/billing to lift the "
                  f"{geocode.FREE_TIER_PER_DAY}/day ceiling.")
        _emit(args.out, "geocode", {k: v for k, v in rep.items() if k != "assertions"}, table)
        return 0

    if args.stage == "places":
        from . import places
        # The same eligibility as geocode — an address and no rooftop coordinate — because this
        # stage exists for exactly the facilities geocode could not place precisely enough. It
        # runs AFTER geocode so that a rooftop answer, when there is one, is already taken and
        # this never competes with it.
        # Every facility with an address, not only the ones missing a coordinate. The coordinate is
        # the narrower prize (892 want one); the website and phone are the broader one — 5,261 have
        # an address and 984 have a website. Matching costs the same read either way.
        need_ids = {r["facility_id"] for r in need_coord}
        todo = [r for r in rows if (r.get("address") or "").strip()
                and (r.get("city") or "").strip() and (r.get("state") or "").strip()]
        boxes = places.state_boxes(rows)
        by_state = collections.Counter((r.get("state") or "").upper() for r in todo)
        # Busiest states first: a run's ceiling should buy the most facilities it can.
        ranked = [s for s, _ in by_state.most_common() if s in boxes]
        chosen, deferred_states = ranked[:args.places_limit], ranked[args.places_limit:]
        no_box = sorted({s for s in by_state if s not in boxes})
        sel = [r for r in todo if (r.get("state") or "").upper() in chosen]
        if args.dry_run:
            _emit(args.out, "places",
                  {"eligible": len(todo), "states_this_run": chosen, "planned": len(sel)},
                  [("eligible", len(todo)), ("states this run", " ".join(chosen)),
                   ("would match", len(sel))])
            return 0
        rep = places.run(sel, places.fetch(chosen, boxes), need_coord=need_ids)
        rep.update(states_this_run=chosen, states_deferred=deferred_states,
                   states_without_a_box=no_box,
                   deferred=sum(by_state[s] for s in deferred_states),
                   boxes={s: [round(v, 3) for v in boxes[s]] for s in chosen})
        (args.out).mkdir(parents=True, exist_ok=True)
        (args.out / "places.assertions.json").write_text(json.dumps(rep["assertions"], default=str))
        table = [("eligible (has an address)", len(todo)),
                 ("of those, wanting a coordinate", len([r for r in todo if r["facility_id"] in need_ids])),
                 ("states this run", " ".join(chosen) or "none"),
                 ("facilities attempted", rep["eligible"]),
                 ("matched to an Overture place", rep["matched"]),
                 ("assertions by field", json.dumps(rep["by_field"])),
                 ("how the address resolved", json.dumps(rep["candidate_shape"])),
                 ("refused", json.dumps(rep["refused"])),
                 ("deferred to the next run", rep["deferred"])]
        if no_box:
            table.append(("states with no coordinate to derive a box from", " ".join(no_box)))
        _emit(args.out, "places", {k: v for k, v in rep.items() if k != "assertions"}, table)
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
        res = footprint.measure(pts, cache=args.out / "overture_index.json",
                                max_files=args.footprint_limit, db=db)
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
               ("file ceiling this run", args.footprint_limit),
               ("deferred to the next run (file ceiling)",
                sum(1 for r in res if r.get("reason") == "deferred: file ceiling reached")),
               ("no building within 30m",
                sum(1 for r in res if (r.get("reason") or "").startswith("no Overture building"))),
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

    if args.stage == "cache":
        from . import cache as lookup_cache
        lookup_cache.ensure(db)
        if args.cache_restore:
            rows = json.loads(Path(args.cache_restore).read_text())
            n = lookup_cache.restore(db, rows)
            _emit(args.out, "cache", {"restored": n}, [("restored from file", n)])
            return 0
        rows = lookup_cache.dump(db)
        if args.cache_dump:
            Path(args.cache_dump).write_text(json.dumps(rows, indent=1, default=str))
        st = lookup_cache.stats(db)
        _emit(args.out, "cache", {"entries": len(rows), "by_kind": st,
                                  "dumped_to": args.cache_dump or None},
              [("entries", len(rows)),
               *[(f"{r['kind']} / {r['provider']}",
                  f"{r['entries']} entries, {r['positives']} positive, "
                  f"{r['calls_saved']} calls saved") for r in st],
               ("dumped to", args.cache_dump or "(not written)")])
        return 0

    if args.stage == "promote":
        from . import promote
        rep = promote.run(db, tag, dry_run=args.dry_run)
        for g in rep["gates"]:
            print(f"  {g}")
        if rep.get("halted"):
            _emit(args.out, "promote", rep,
                  [("HALTED", "gate E6 failed"), *[("gate", g) for g in rep["gates"]]])
            print("\nHALT: the rebuilt golden covered less than the one it would replace; "
                  "nothing was written", file=sys.stderr)
            return 1
        _emit(args.out, "promote", rep,
              [("assertions read", rep["assertions_read"]),
               ("golden rows built", rep["golden_rows"]),
               ("golden rows written", rep.get("would_write", rep["written"])),
               ("conflicts", rep["conflicts"]),
               ("survivorship version", rep["survivorship_version"]),
               ("columns added", json.dumps(rep["columns_added"])),
               ("coverage gained", json.dumps(rep["gained"])),
               *[("gate", g) for g in rep["gates"]]])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
