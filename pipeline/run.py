"""The run. One command, layers 1–8 in order, halts at the first failing gate.

    python -m pipeline.run --registry registry/sources.yaml [--layers 2-8] [--dry-run] [--rerun]

Layer 1 is skipped with --layers 2-8 (use existing ic-csv/). --rerun asserts identical inputs
so G3 tests id stability. Every run writes run_records/<timestamp>.json whatever happens; a
successful release also loads the warehouse (pipeline/warehouse.py — SQLite now, BigQuery later).
"""
from __future__ import annotations
import argparse, csv, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

from .heartbeat import Heartbeat
from . import __version__, ai_enabled, ai_client_and_model, acquire, validate, classify, resolve, reconcile, golden, gates, measure, warehouse
from .registry import load_yaml, active_sources, sha256_file, registry_version

ROOT = Path(__file__).resolve().parent.parent


def _layers(spec: str) -> set[int]:
    a, _, b = spec.partition("-")
    return set(range(int(a), int(b or a) + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default="registry/sources.yaml")
    ap.add_argument("--config", default="registry/config.yaml")
    ap.add_argument("--layers", default="1-8")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rerun", action="store_true", help="inputs identical to last run; G3 must issue 0 ids")
    ap.add_argument("--limit", type=int, default=0,
                    help="classify only the first N candidates. A short run on the REAL path — same "
                         "100-row batches, same rows, same validation — so what it measures "
                         "(IC share, secs/batch, re-asks) actually predicts a full run. The 60-seed "
                         "bake-off does not: it under-read ling's latency by 3.5x and reported zero "
                         "re-asks for models needing them on a quarter of real batches.")
    ap.add_argument("--out", default="build")
    args = ap.parse_args(argv)

    reg = load_yaml(ROOT / args.registry)
    cfg = load_yaml(ROOT / args.config)
    # Compare models without churning the committed pin. Flipping registry/config.yaml for every
    # A/B puts the model in the git history as a decision when it was only a trial, and the
    # release tag already records which model actually ran.
    if os.environ.get("IC_CLASSIFIER_MODEL"):
        cfg["classifier"]["model"] = os.environ["IC_CLASSIFIER_MODEL"]
    layers = _layers(args.layers)
    started = datetime.now(timezone.utc)
    out = ROOT / args.out; out.mkdir(exist_ok=True)
    # A probe's record belongs with its probe output, not in the release history.
    rec_dir = out if args.limit else None
    csv_dir, norm_dir = ROOT / "ic-csv", out / "normalised"
    record = {
        "pipeline_version": __version__, "started": started.isoformat(),
        "registry_version": registry_version(ROOT), "registry_file_sha": sha256_file(ROOT / args.registry),
        "control_sha": (ROOT / cfg["control"]["checksum_path"]).read_text().split()[0] if (ROOT / cfg["control"]["checksum_path"]).exists() else None,
        "layers": {}, "gates": [], "halted_at": None,
    }
    sources = active_sources(reg)
    print(f"IC pipeline {__version__} · registry {record['registry_version']} · {len(sources)} active sources · layers {sorted(layers)}")

    # Preflight the AI credential before a single byte is downloaded. Without this the first call
    # that needs a key is Layer 3, an hour and a 1.27 GB EPA pull later, and the run dies having
    # thrown away the whole acquisition for a missing environment variable.
    if 3 in layers and ai_enabled() and not args.dry_run:
        try:
            _, _model, _provider = ai_client_and_model(cfg["classifier"]["model"])
            print(f"  ai on · classifier {_model} via {_provider}")
        except RuntimeError as e:
            print(f"HALT before layer 1: {e}", file=sys.stderr)
            print("       set AI_GATEWAY_API_KEY (or ANTHROPIC_API_KEY), or run with IC_AI=off",
                  file=sys.stderr)
            return 2

    # Published to the blob store so a run can be watched from outside while it is still going.
    # GitHub serves no logs for an in-progress job, so without this the only way to read a live
    # run's state was to cancel it. Inert when no blob token is set; never raises.
    hb = Heartbeat(cfg)
    hb.beat("starting", force=True, registry_version=record["registry_version"],
            pipeline_version=__version__, layers=sorted(layers), ai="on" if ai_enabled() else "off")

    def halt(where: str, why: str) -> int:
        record["halted_at"] = where; record["halt_reason"] = why
        hb.failed(f"halted at {where}: {why}", phase=where)
        _write_record(record, rec_dir); print(f"HALT at {where}: {why}", file=sys.stderr); return 2

    if args.dry_run:
        for s in sources:
            print(f"  would pull {s['id']:<22} class {s['class']} via {s['method']:<9} classify={s.get('needs_classify', False)}")
        _write_record(record, rec_dir); return 0

    # ---- Layer 1
    hb.beat("1_acquire")
    if 1 in layers:
        pulled, failed, skipped = [], {}, {}
        for s in sources:
            try:
                pulled.append(str(acquire.pull_source(s, cfg, csv_dir, ROOT / cfg["storage"]["local_cache"])))
            except acquire.SourceSkipped as e:
                skipped[s["id"]] = str(e)
            except (acquire.SourceNotImplemented, acquire.SourceFailed) as e:
                failed[s["id"]] = str(e)
        record["layers"]["1_acquire"] = {"pulled": pulled, "failed": failed, "skipped": skipped}
        for sid, why in sorted(skipped.items()):
            print(f"  SKIP {sid}: {why}")
        if failed:
            return halt("layer 1", f"{len(failed)} active sources did not pull: " + "; ".join(f"{k} — {v}" for k, v in sorted(failed.items())))

    # ---- Layer 2
    hb.beat("2_validate")
    last_run = _last_run_dir(ROOT / "run_records")
    v = validate.run(csv_dir, norm_dir, last_run, cfg["validate"]["row_count_drift_tolerance"])
    record["layers"]["2_validate"] = v
    if v["halted"]:
        return halt("layer 2", f"sources halted: {v['halted']} — {v['problems']}")
    rows = []
    for p in sorted(norm_dir.glob("*.csv")):
        rows += list(csv.DictReader(open(p, newline="", encoding="utf-8")))
    for r in rows:
        r["no_fixed_plant"] = r.get("no_fixed_plant") == "True"
    if not rows:
        # No source rows at all is an input failure, not an empty dataset. Without this the run
        # goes green on nothing: every gate passes vacuously, Layer 8 tags a release with
        # published_count 0, and the workflow opens a release PR for it. That is exactly what
        # `--layers 2-8` does in CI, where ic-csv/*.csv is gitignored and so never checked out.
        return halt("layer 2", f"no rows from any source — {csv_dir.name}/ held no contract CSV to "
                               f"normalise. With --layers 2-8 the run reads ic-csv/, which is "
                               f"gitignored and absent on a fresh checkout: run layers 1-8 instead.")

    # ---- Layer 3 (class B only)
    needs = {s["id"] for s in sources if s.get("needs_classify")}
    seeds = _load_seeds(ROOT / "control" / "seeds.csv")
    labels, cls_meta = {}, None
    if 3 in layers and needs:
        core = set(cfg["frame"]["naics"])
        # Candidate generation is deterministic (keyword x NAICS matrix) and runs either way — it is
        # the measurable half of Layer 3, and with the classifier off it is what the review queue holds.
        cand = classify.candidates([r for r in rows if r["source_id"] in needs], core)
        if args.limit:
            # Deterministic slice: candidates are already ordered by source and row position, so
            # the same --limit always probes the same rows and two models are compared on
            # identical input. Seeds are added by classify.run regardless, so G5 still scores.
            print(f"  --limit {args.limit}: probing {min(args.limit, len(cand))} of {len(cand)} "
                  f"candidates (not a release)")
            cand = cand[:args.limit]
        if ai_enabled():
            cls_meta = classify.run(cand, cfg["classifier"], seeds, out / "classify_cache",
                                    ROOT / cfg["classifier"]["prompt_path"], hb=hb)
            labels = cls_meta["labels"]
            record["layers"]["3_classify"] = {k: v for k, v in cls_meta.items() if k != "labels"}
            keep, review, drop = [], [], 0
            for r in rows:
                if r["source_id"] not in needs:
                    keep.append(r); continue
                lab = labels.get(r["row_hash"], {}).get("label")
                if lab == "IC": keep.append(r)
                elif lab == "UNCERTAIN": review.append(r)
                else: drop += 1
            _write_csv(out / "review_queue.csv", review)
            record["layers"]["3_classify"].update({"ic": len(keep), "uncertain": len(review), "not_ic_or_uncandidated": drop})
            rows = keep
        else:
            # IC_AI=off. Nothing is labelled, so nothing may be admitted as IC and nothing may be
            # dropped as NOT-IC: every candidate goes to the review queue and the rest of the run
            # proceeds on the sources that need no classifier. Placeholder, and it says so.
            cand_hashes = {r["row_hash"] for r in cand}
            keep = [r for r in rows if r["source_id"] not in needs]
            review = [r for r in rows if r["source_id"] in needs and r["row_hash"] in cand_hashes]
            noncand = sum(1 for r in rows if r["source_id"] in needs and r["row_hash"] not in cand_hashes)
            _write_csv(out / "review_queue.csv", review)
            record["layers"]["3_classify"] = {
                "skipped": "IC_AI=off — classifier not run", "sources": sorted(needs),
                "rows_from_those_sources": sum(1 for r in rows if r["source_id"] in needs),
                "n_candidates": len(cand), "uncertain": len(review), "not_candidated": noncand,
                "ic": 0, "note": "candidate generation is deterministic and did run; every candidate "
                                 "is parked in review_queue.csv awaiting a classified run",
            }
            print(f"  SKIP layer 3 classifier (IC_AI=off): {len(cand)} deterministic candidates "
                  f"from {sorted(needs)} parked in review_queue.csv")
            rows = keep

    # ---- Layer 4
    hb.beat("4_resolve")
    crosswalk = _load_crosswalk(ROOT / "control" / "crosswalk.csv")
    record["layers"]["4_resolve"] = resolve.run(rows, crosswalk.get("rows", {}))

    # ---- Layer 5
    hb.beat("5_reconcile")
    rec = reconcile.run(rows, ROOT / "id_registry.json")
    facilities = rec["facilities"]
    record["layers"]["5_reconcile"] = {k: v for k, v in rec.items() if k not in {"facilities", "rows"}}
    _write_csv(out / "facilities.csv", facilities)
    _write_csv(out / "rows_reconciled.csv", rec["rows"])

    # ---- Layer 5b: assertions → golden table (pure function of assertions + ids + survivorship rules)
    rules = load_yaml(ROOT / "registry" / "survivorship.yaml")
    src_class = {s["id"]: s["class"] for s in reg["sources"]}
    asserts = golden.assertions_from_rows(rec["rows"], src_class) + golden.load_operator_assertions(ROOT / "control" / "operator_assertions.csv")
    gold, conflicts = golden.build_golden(asserts, rules)
    _write_csv(out / "assertions.csv", asserts)
    _write_csv(out / "golden.csv", gold)
    _write_csv(out / "conflicts.csv", conflicts)
    record["layers"]["5b_golden"] = {"assertions": len(asserts), "golden_rows": len(gold), "conflicts": len(conflicts),
                                     "survivorship_version": rules.get("version"), "operator_assertions": sum(1 for a in asserts if a["source_id"] == "operator")}

    # ---- Layer 6
    hb.beat("6_gates")
    g = cfg["gates"]
    results = [
        gates.g1_dedupe(facilities, g["g1_dedupe_max_rate"], g["g1_thresholds"], out / f"dedupe_audit_{started:%Y-%m-%d}.csv"),
        gates.g2_false_merge(rec["rows"]),
        gates.g3_id_stability(rec["ids_issued"], g["g3_allow_new_ids_on_rerun"], args.rerun),
        gates.g4_control_isolation(ROOT / cfg["control"]["path"], ROOT / cfg["control"]["checksum_path"], rec["rows"]),
        gates.g5_classifier_eval(labels, seeds, g["g5_min_precision"], g["g5_min_recall"]) if (cls_meta and cls_meta["n_candidates"])
        else gates.GateResult("G5 classifier eval", True, "no rows were classified in this run — nothing to audit"),
    ]
    record["gates"] = [{"gate": r.gate, "passed": r.passed, "summary": r.summary, "details": r.details} for r in results]
    for r in results:
        print(f"  {'PASS' if r.passed else 'FAIL'}  {r.gate}: {r.summary}")
    failed = [r for r in results if not r.passed]
    if failed:
        return halt("layer 6", "; ".join(f"{r.gate} — {r.summary}" for r in failed))

    # ---- Layer 7
    hb.beat("7_measure")
    if 7 not in layers:
        print(f"  layers {sorted(layers)}: stopping before layer 7")
        _write_record(record, rec_dir); return 0
    frame_path = ROOT / "control" / "frame_state_totals.csv"
    control_rows = list(csv.DictReader(open(ROOT / cfg["control"]["path"], newline=""))) if (ROOT / cfg["control"]["path"]).exists() else []
    m = {"recall": measure.recall(control_rows, facilities, crosswalk.get("control", {}))}
    if frame_path.exists():
        m["coverage_bias"] = measure.coverage_and_bias(facilities, measure.load_frame(frame_path), tuple(cfg["measure"]["bias_band"]), cfg["measure"]["bias_min_state_share"])
        gaps = load_yaml(ROOT / "registry" / "known-gaps.yaml") if (ROOT / "registry" / "known-gaps.yaml").exists() else {}
        m["coverage_bias"]["out_of_band_causes"] = {st: gaps.get("states", {}).get(st, "NO CAUSE ON RECORD") for st in m["coverage_bias"]["out_of_band"]}
    record["layers"]["7_measure"] = m

    # ---- Layer 8
    if 8 not in layers:
        # --layers only ever gated layers 1 and 3; everything downstream ran regardless, so
        # `--layers 1-7` still loaded the warehouse. Runs 35163831460 and 35164672039 were
        # dispatched as 1-7 precisely to keep them out of Neon while another run published, and
        # that guarantee did not exist. A flag that silently ignores the one layer with external
        # side effects is worse than no flag.
        print(f"  layers {sorted(layers)}: stopping before layer 8, nothing written to the warehouse")
        hb.done(phase="stopped_before_warehouse", gates=[{"id": r.gate, "passed": r.passed} for r in results])
        _write_record(record, rec_dir)
        return 0
    hb.beat("8_warehouse")
    g1 = results[0].details
    record["release"] = {
        "published_count": g1.get("corrected_count", len(facilities)), "raw_count": len(facilities),
        "tag": f"v{__version__}+reg.{record['registry_version']}+ids.{sha256_file(ROOT / 'id_registry.json')[:8]}+ctl.{(record['control_sha'] or 'none')[:8]}+surv.{sha256_file(ROOT / 'registry' / 'survivorship.yaml')[:8]}"
               + (f"+prompt.{cls_meta['prompt_hash']}+model.{cls_meta['model']}" if cls_meta else ""),
        "finished": datetime.now(timezone.utc).isoformat(),
        "ai": "on" if ai_enabled() else "off — deterministic sources only, classifier and ai_extraction skipped",
    }
    try:
        wh = warehouse.open_warehouse(cfg, ROOT)
        if wh is not None:
            record["release"]["warehouse"] = wh.load_release(
                record, assertions=asserts, golden=gold, conflicts=conflicts, facilities=facilities, rows=rec["rows"],
                registry=reg, registry_text=(ROOT / args.registry).read_text(), rules=rules, control_rows=control_rows,
                control_sha=record["control_sha"], survivorship_hash=sha256_file(ROOT / "registry" / "survivorship.yaml"),
                known_gaps=load_yaml(ROOT / "registry" / "known-gaps.yaml") if (ROOT / "registry" / "known-gaps.yaml").exists() else {})
            wh.close()
            print(f"  warehouse {wh.engine}: {record['release']['warehouse']['assertions_appended']} assertions appended → {record['release']['warehouse']['path']}")
    except warehouse.WarehouseNotImplemented as e:
        return halt("layer 8", str(e))
    _write_record(record, rec_dir)
    print(f"RELEASE {record['release']['tag']} · {record['release']['published_count']} facilities")
    hb.done(phase="released", release_tag=record["release"]["tag"],
            published_count=record["release"]["published_count"],
            gates=[{"id": g.get("id"), "passed": g.get("passed")} for g in record.get("gates", [])])
    return 0


def _write_record(record: dict, into: Path | None = None) -> None:
    """Write the run record. `into` diverts a probe's record out of the release history.

    run_records/ is provenance for releases: every file in it should be a run that was trying to
    publish. A --limit probe is a model trial over a slice, so its record goes next to its own
    build output instead, which is gitignored. Five probes in an evening would otherwise bury the
    real history in experiments that were never meant to ship.
    """
    d = into or (ROOT / "run_records"); d.mkdir(parents=True, exist_ok=True)
    p = d / f"{record['started'][:19].replace(':', '')}.json"
    p.write_text(json.dumps(record, indent=1, default=str))


def _last_run_dir(records: Path) -> Path | None:
    return None  # pull.json files are kept per run under ic-csv/ in v1.0; drift check wires here in v1.1


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text(""); return
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def _load_seeds(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return list(csv.DictReader(open(path, newline="", encoding="utf-8")))


def _load_crosswalk(path: Path) -> dict:
    """control/crosswalk.csv: control_id,row_hash,facility_id — explicit links beat string similarity."""
    out = {"rows": {}, "control": {}}
    if not path.exists():
        return out
    for r in csv.DictReader(open(path, newline="")):
        if r.get("row_hash"): out["rows"][r["row_hash"]] = r.get("legal_entity_id") or r.get("facility_id") or ""
        if r.get("control_id") and r.get("facility_id"): out["control"][r["control_id"]] = r["facility_id"]
    return out


if __name__ == "__main__":
    sys.exit(main())
