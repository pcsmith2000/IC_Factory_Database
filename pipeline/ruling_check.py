"""Did people's rulings land? A check that golden agrees with control/operator_assertions.csv.

A ruling that a plant is not IC or closed only helps if it reaches golden. Nothing checked that it
had: the spot check of 2026-10-13 found three test rows and an Alberta plant still published ten days
after the owner ruled test rows and non-US plants out, because nobody had written the rulings down as
lines of the file. This check cannot write a missing line, but it does two things:

- **fails** when a facility whose latest `existence_flag` ruling in the file is `not_ic` or `closed`
  is still in golden. A facility still queued in golden_dirty is pending, not a failure, and an ADL
  employee's later `active` through ADL_Viz outranks the ruling on purpose (golden.py), so neither
  counts;
- **warns** about golden rows that look like what the standing scope rulings exclude but carry no
  ruling yet: a name that reads as a test record, or a `country_based` other than the US.

    python -m pipeline.ruling_check [--json out.json]   # exit 1 when a ruling has not landed
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

from .golden import EXCLUDING_FLAGS
from .operator_apply import PATH, read

TEST_NAME = re.compile(r"(?i)\b(?:[a-z]*prod)?test\b|\bbotest\b|\bplaceholder\b")
US = {"", "US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}


def excluding_rulings(rows: list[dict]) -> dict[str, dict]:
    """facility_id -> its latest existence_flag line, kept only when that line takes the plant out."""
    latest: dict[str, dict] = {}
    for r in rows:
        if r.get("field") != "existence_flag":
            continue
        fid = r["facility_id"]
        if fid not in latest or r["retrieved_date"] >= latest[fid]["retrieved_date"]:
            latest[fid] = r
    return {fid: r for fid, r in latest.items() if r["value"].strip() in EXCLUDING_FLAGS}


def unapplied(golden: list[dict], rulings: dict[str, dict], pending: set[str]) -> list[dict]:
    out = []
    for g in golden:
        fid = g["facility_key"]
        if fid not in rulings or fid in pending:
            continue
        if g.get("existence_flag__source") == "adl_employee_feedback":
            continue
        r = rulings[fid]
        out.append({"facility_id": fid, "name": g.get("name"), "ruling": r["value"].strip(),
                    "ruled_on": r["retrieved_date"], "golden_existence_flag": g.get("existence_flag")})
    return out


def suspects(golden: list[dict], rulings: dict[str, dict]) -> list[dict]:
    out = []
    for g in golden:
        if g["facility_key"] in rulings:
            continue
        why = []
        if TEST_NAME.search(g.get("name") or ""):
            why.append("name reads as a test record")
        if (g.get("country_based") or "").strip().upper() not in US:
            why.append(f"country_based is {g['country_based']!r}")
        if why:
            out.append({"facility_id": g["facility_key"], "name": g.get("name"), "why": "; ".join(why)})
    return out


def check(wh, path: Path = PATH) -> dict:
    rulings = excluding_rulings(read(path))
    golden = wh.query("SELECT facility_key, name, existence_flag, existence_flag__source, country_based FROM golden_facility")
    pending = {r["facility_key"] for r in wh.query("SELECT facility_key FROM golden_dirty")}
    return {"excluding_rulings": len(rulings), "unapplied": unapplied(golden, rulings, pending),
            "suspects": suspects(golden, rulings)}


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    from .warehouse import open_warehouse, connect as connect_url
    ap = argparse.ArgumentParser(prog="python -m pipeline.ruling_check")
    ap.add_argument("--db", default=None, help="Postgres URL; default: DATABASE_URL")
    ap.add_argument("--json", default=None, help="also write the report to this file")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    wh = connect_url(args.db) if args.db else open_warehouse(load_yaml(root / "registry" / "config.yaml"), root)
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    rep = check(wh)
    print(json.dumps(rep, indent=1, default=str))
    if args.json:
        Path(args.json).write_text(json.dumps(rep, indent=1, default=str))
    for s in rep["suspects"]:
        print(f"::warning::{s['facility_id']} {s['name']}: {s['why']} and no ruling in control/operator_assertions.csv")
    for u in rep["unapplied"]:
        print(f"::error::{u['facility_id']} {u['name']}: ruled {u['ruling']} on {u['ruled_on']} but still in golden")
    return 1 if rep["unapplied"] else 0


if __name__ == "__main__":
    sys.exit(main())
