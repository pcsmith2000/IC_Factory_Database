"""The run. One command, layers 1–8 in order, halts at the first failing gate.

    python -m pipeline.run --registry registry/sources.yaml [--layers 2-8] [--dry-run] [--rerun]

Layer 1 is skipped with --layers 2-8 (use existing ic-csv/). --rerun asserts identical inputs
so G3 tests id stability. Every run writes run_records/<timestamp>.json whatever happens; a
successful release also loads the warehouse (pipeline/warehouse.py — SQLite now, BigQuery later).
"""
from __future__ import annotations
import argparse, csv, json, sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, acquire, validate, classify, resolve, reconcile, golden, gates, measure, warehouse
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
    ap.add_argument("--out", default="build")
    args = ap.parse_args(argv)

    reg = load_yaml(ROOT / args.registry)
    cfg = load_yaml(ROOT / args.config)
    layers = _layers(args.layers)
    started = datetime.now(timezone.utc)
    out = ROOT / args.out; out.mkdir(exist_ok=True)
    csv_dir, norm_dir = ROOT / "ic-csv", out / "normalised"
    record = {
        "pipeline_version": __version__, "started": started.isoformat(),
        "registry_version": registry_version(ROOT), "registry_file_sha": sha256_file(ROOT / args.registry),
        "control_sha": (ROOT / cfg["control"]["checksum_path"]).read_text().split()[0] if (ROOT / cfg["control"]["checksum_path"]).exists() else None,
        "layers": {}, "gates": [], "halted_at": None,
    }
    sources = active_sources(reg)
    print(f"IC pipeline {__version__} · registry {record['registry_version']} · {len(sources)} active sources · layers {sorted(layers)}")

    def halt(where: str, why: str) -> int:
        record["halted_at"] = where; record["halt_reason"] = why
        _write_record(record); print(f"HALT at {where}: {why}", file=sys.stderr); return 2

    if args.dry_run:
        for s in sources:
            print(f"  would pull {s['id']:<22} class {s['class']} via {s['method']:<9} classify={s.get('needs_classify', False)}")
        _write_record(record); return 0

    # ---- Layer 1
    if 1 in layers:
        pulled, failed = [], {}
        for s in sources:
            try:
                pulled.append(str(acquire.pull_source(s, cfg, csv_dir, ROOT / cfg["storage"]["local_cache"])))
            except (acquire.SourceNotImplemented, acquire.SourceFailed) as e:
                failed[s["id"]] = str(e)
        record["layers"]["1_acquire"] = {"pulled": pulled, "failed": failed}
        if failed:
            return halt("layer 1", f"{len(failed)} active sources did not pull: " + "; ".join(f"{k} — {v}" for k, v in sorted(failed.items())))

    # ---- Layer 2
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

    # ---- Layer 3 (class B only)
    needs = {s["id"] for s in sources if s.get("needs_classify")}
    seeds = _load_seeds(ROOT / "control" / "seeds.csv")
    labels, cls_meta = {}, None
    if 3 in layers and needs:
        core = set(cfg["frame"]["naics"])
        cand = classify.candidates([r for r in rows if r["source_id"] in needs], core)
        cls_meta = classify.run(cand, cfg["classifier"], seeds, out / "classify_cache", ROOT / cfg["classifier"]["prompt_path"])
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

    # ---- Layer 4
    crosswalk = _load_crosswalk(ROOT / "control" / "crosswalk.csv")
    record["layers"]["4_resolve"] = resolve.run(rows, crosswalk.get("rows", {}))

    # ---- Layer 5
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
    frame_path = ROOT / "control" / "frame_state_totals.csv"
    control_rows = list(csv.DictReader(open(ROOT / cfg["control"]["path"], newline=""))) if (ROOT / cfg["control"]["path"]).exists() else []
    m = {"recall": measure.recall(control_rows, facilities, crosswalk.get("control", {}))}
    if frame_path.exists():
        m["coverage_bias"] = measure.coverage_and_bias(facilities, measure.load_frame(frame_path), tuple(cfg["measure"]["bias_band"]), cfg["measure"]["bias_min_state_share"])
        gaps = load_yaml(ROOT / "registry" / "known-gaps.yaml") if (ROOT / "registry" / "known-gaps.yaml").exists() else {}
        m["coverage_bias"]["out_of_band_causes"] = {st: gaps.get("states", {}).get(st, "NO CAUSE ON RECORD") for st in m["coverage_bias"]["out_of_band"]}
    record["layers"]["7_measure"] = m

    # ---- Layer 8
    g1 = results[0].details
    record["release"] = {
        "published_count": g1.get("corrected_count", len(facilities)), "raw_count": len(facilities),
        "tag": f"v{__version__}+reg.{record['registry_version']}+ids.{sha256_file(ROOT / 'id_registry.json')[:8]}+ctl.{(record['control_sha'] or 'none')[:8]}+surv.{sha256_file(ROOT / 'registry' / 'survivorship.yaml')[:8]}"
               + (f"+prompt.{cls_meta['prompt_hash']}+model.{cls_meta['model']}" if cls_meta else ""),
        "finished": datetime.now(timezone.utc).isoformat(),
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
    _write_record(record)
    print(f"RELEASE {record['release']['tag']} · {record['release']['published_count']} facilities")
    return 0


def _write_record(record: dict) -> None:
    d = ROOT / "run_records"; d.mkdir(exist_ok=True)
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
