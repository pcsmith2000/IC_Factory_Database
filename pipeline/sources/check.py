"""Run one fetcher on its own and validate the result against the contract.

    python -m pipeline.sources.check <source_id>                 fetch → parse → validate → ic-csv/<id>.csv
    python -m pipeline.sources.check <source_id> --file <path>   parse an already-downloaded file (no network)
    python -m pipeline.sources.check <source_id> --file a.pdf --file b.pdf

Prints the row count, the first rows, and every contract problem. Exit 1 on any problem.
"""
from __future__ import annotations
import argparse, importlib, sys
from datetime import date
from pathlib import Path
from ..contract import COLUMNS, validate_rows, write_rows
from ..registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.sources.check")
    ap.add_argument("source_id")
    ap.add_argument("--file", action="append", help="parse this local file instead of fetching (repeatable)")
    ap.add_argument("--out", default=None, help="write the contract CSV here (default ic-csv/<id>.csv)")
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args(argv)

    reg, cfg = load_yaml(ROOT / "registry" / "sources.yaml"), load_yaml(ROOT / "registry" / "config.yaml")
    source = next((s for s in reg["sources"] if s["id"] == args.source_id), None)
    if not source:
        print(f"{args.source_id}: not in registry/sources.yaml", file=sys.stderr); return 1
    mod = importlib.import_module(f"pipeline.sources.{args.source_id}")
    archive = ROOT / cfg["storage"]["local_cache"] / args.source_id / date.today().isoformat()
    if args.file:
        rows = mod.parse([Path(f) for f in args.file], source)
    else:
        rows = mod.parse(mod.fetch(source, cfg, archive), source)
    for r in rows:
        for c in COLUMNS:
            r.setdefault(c, "")
    problems = validate_rows(args.source_id, rows)
    print(f"{args.source_id}: {len(rows)} rows · {sum(1 for r in rows if r['address_verbatim'])} with an address · {len(problems)} problems")
    for r in rows[:args.show]:
        print("  ", {k: r[k] for k in ("row_position", "name_verbatim", "address_verbatim", "city_verbatim", "state_verbatim", "zip_verbatim", "source_identifier", "expiry_date") if r.get(k)})
    for p in problems[:20]:
        print("  PROBLEM", p)
    if not rows:
        print("  PROBLEM no rows parsed"); problems.append("no rows")
    out = Path(args.out) if args.out else ROOT / "ic-csv" / f"{args.source_id}.csv"
    write_rows(out, rows, COLUMNS)
    print(f"  wrote {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
