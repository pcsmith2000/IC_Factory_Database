"""Operator assertions: people's rulings (control/operator_assertions.csv) reach the warehouse.

A full pipeline run reads the file at Layer 5b, but golden-refresh builds golden from fact_assertions
alone, so between full runs a ruling that a plant is not IC (or closed, or has a confirmed pin) reached
nobody. `apply` appends every line to fact_assertions as source `operator` (class `operator`), the
highest-ranked source golden.py knows, under the current release (idempotent: one assertion id per
facility, field, value and date). The fact_assertions trigger queues each facility in golden_dirty,
golden-refresh carries the class across releases (golden_refresh CARRIED_CLASSES) and brings the
ruling into golden; a not_ic or closed ruling takes the plant out (golden.split_excluded).

To undo a ruling, add a line with the right value and a later retrieved_date; never delete a line.

    python -m pipeline.operator_apply [--dry-run]
"""
from __future__ import annotations
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .monitor_fix import _h

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "control" / "operator_assertions.csv"
SOURCE = "operator"


def read(path: Path = PATH) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _row_hash(r: dict) -> str:
    return _h(SOURCE, r["facility_id"], r["field"], r["value"].strip(), r["retrieved_date"])


def apply(wh, *, path: Path = PATH, dry_run: bool = False) -> dict:
    from .golden_refresh import current_release
    from .warehouse import SYNTHETIC_SOURCES, _date_row
    tag = current_release(wh)
    active = {r["facility_id"] for r in wh.query("SELECT facility_id FROM facility WHERE status = 'active'")}
    rows = read(path)
    now = datetime.now(timezone.utc).isoformat()
    refs, facts, skipped = [], [], []
    for r in rows:
        if r["facility_id"] not in active:
            skipped.append({"facility_id": r["facility_id"], "field": r["field"], "reason": "not an active registered facility"})
            continue
        rh, v = _row_hash(r), r["value"].strip()
        refs.append((rh, SOURCE, None, r.get("note") or "", r["retrieved_date"], r["field"], "operator",
                     None, None, None, None, None, r["facility_id"], SOURCE, 1.0, tag))
        facts.append((_h(SOURCE, "assertion", r["facility_id"], r["field"], v, r["retrieved_date"]), tag, r["facility_id"],
                      SOURCE, r["field"], r["retrieved_date"], v, SOURCE, 0, rh, 1.0, SOURCE, now))
    existing = set()
    if facts:
        marks = ",".join("?" * len(facts))
        existing = {x["assertion_id"] for x in wh.query(
            f"SELECT assertion_id FROM fact_assertions WHERE release_tag = ? AND assertion_id IN ({marks})",
            (tag, *[f[0] for f in facts]))}
    new = [f for f in facts if f[0] not in existing]
    if new and not dry_run:
        with wh.transaction() as c:
            c.executemany("INSERT INTO dim_source VALUES (?,?,?,?,?,?,?) ON CONFLICT (source_key) DO NOTHING",
                          [(SOURCE, SOURCE, SYNTHETIC_SOURCES[SOURCE]["name"], SOURCE, "human_review", "human_verified", "active")])
            c.executemany("INSERT INTO dim_date VALUES (?,?,?,?) ON CONFLICT (date_key) DO NOTHING",
                          [d for d in {_date_row(f[5]) for f in new} if d])
            c.executemany("INSERT INTO ref_source_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT (row_hash) DO NOTHING", refs)
            c.executemany("INSERT INTO fact_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT (assertion_id, release_tag) DO NOTHING", new)
    return {"release_tag": tag, "lines": len(rows), "written": len(new), "already_present": len(existing),
            "skipped": skipped, "dry_run": dry_run}


def main(argv=None) -> int:
    import argparse
    from . import control
    from .registry import load_yaml
    from .warehouse import open_warehouse, SqliteWarehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.operator_apply")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    ap.add_argument("--dry-run", action="store_true", help="report what would be written; write nothing")
    args = ap.parse_args(argv)
    cfg = load_yaml(ROOT / "registry" / "config.yaml")
    bad = [p for p in control.check(cfg) if p.startswith("operator_assertions.csv")]
    if bad:
        print("\n".join(bad), file=sys.stderr); return 1
    wh = SqliteWarehouse(Path(args.db)) if args.db else open_warehouse(cfg, ROOT)
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    print(json.dumps(apply(wh, dry_run=args.dry_run), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
