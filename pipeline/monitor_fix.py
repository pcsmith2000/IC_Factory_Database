"""Monitor fixes: small, evidenced data corrections from the automated warehouse monitor.

The hourly monitor (a Claude session that samples the warehouse read-only) finds defects a source
shipped: a state column shifted two places alphabetically, a 9-digit ZIP with no dash. It records
each correction as one line of control/monitor_fix_assertions.csv:

    facility_id,field,value,retrieved_date,issue,evidence

`issue` is the GitHub issue that describes the defect and `evidence` says why the value is right
(for example "ZIP 77020 and city Houston are Texas"). A monitor fix is not a human decision, so:

  * it is its own source, `monitor_fix`, never `operator`: people outrank it (adl_employee_feedback,
    operator); it outranks a site visit, web-research overrides and every automated source. When a
    later source disagrees, the monitor reviews the conflict and updates or removes its line;
  * it may only correct the location and contact fields in FIELDS. It never rules a facility out
    (existence_flag), never moves a pin (lat_lon), never renames or reclassifies a plant.

`apply` appends the lines to fact_assertions under the current release (idempotent: one assertion id
per facility, field and value). The fact_assertions trigger queues each facility in golden_dirty and
golden-refresh brings it into golden. The class is carried across releases (golden_refresh
CARRIED_CLASSES), and a full pipeline run reads the same file at Layer 5b. To undo a fix, add a line
with the right value; the later retrieved_date wins.

    python -m pipeline.monitor_fix --check           # validate the file, touch nothing
    python -m pipeline.monitor_fix [--dry-run]       # append to the warehouse
"""
from __future__ import annotations
import csv
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "control" / "monitor_fix_assertions.csv"
SOURCE = "monitor_fix"
COLUMNS = ["facility_id", "field", "value", "retrieved_date", "issue", "evidence"]
FIELDS = ("address", "city", "state", "zip", "website", "phone", "email")
_VALUE = {
    "state": re.compile(r"^[A-Z]{2}$"),
    "zip": re.compile(r"^\d{5}(-\d{4})?$"),
    "website": re.compile(r"^https?://\S+$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
}


def _h(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


def read(path: Path = PATH) -> tuple[list[str], list[dict]]:
    if not path.exists():
        return COLUMNS, []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def problems(path: Path = PATH) -> list[str]:
    hdr, rows = read(path)
    out = []
    if hdr != COLUMNS:
        out.append(f"{path.name}: columns must be {','.join(COLUMNS)}, got {hdr}")
        return out
    for i, r in enumerate(rows, 2):
        where = f"{path.name} line {i}"
        if not re.match(r"^IC-\d{5}$", r.get("facility_id") or ""):
            out.append(f"{where}: facility_id {r.get('facility_id')!r} is not an IC-number")
        if r.get("field") not in FIELDS:
            out.append(f"{where}: field {r.get('field')!r} is not one a monitor may fix {list(FIELDS)}")
        pat = _VALUE.get(r.get("field") or "")
        if not (r.get("value") or "").strip() or (pat and not pat.match(r["value"])):
            out.append(f"{where}: value {r.get('value')!r} is not a valid {r.get('field')}")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", r.get("retrieved_date") or ""):
            out.append(f"{where}: retrieved_date must be YYYY-MM-DD")
        if not re.search(r"(^#\d+$)|(/issues/\d+$)", r.get("issue") or ""):
            out.append(f"{where}: issue must name the GitHub issue (#57 or its URL)")
        if len((r.get("evidence") or "").strip()) < 10:
            out.append(f"{where}: evidence must say why the value is right")
    return out


def load(path: Path = PATH) -> list[dict]:
    """The file as golden.py assertions (Layer 5b of a full run)."""
    _, rows = read(path)
    return [{"facility_id": r["facility_id"], "field": r["field"], "value": r["value"].strip(), "source_id": SOURCE,
             "source_class": SOURCE, "retrieved_date": r["retrieved_date"], "row_hash": _row_hash(r),
             "basis": SOURCE, "site_visit": False, "confidence": 1.0} for r in rows]


def _row_hash(r: dict) -> str:
    return _h(SOURCE, r["facility_id"], r["field"], r["value"].strip(), r["retrieved_date"])


def apply(wh, *, path: Path = PATH, dry_run: bool = False) -> dict:
    from .golden_refresh import current_release
    from .warehouse import SYNTHETIC_SOURCES, _date_row
    bad = problems(path)
    if bad:
        raise ValueError("; ".join(bad))
    tag = current_release(wh)
    active = {r["facility_id"] for r in wh.query("SELECT facility_id FROM facility WHERE status = 'active'")}
    _, rows = read(path)
    now = datetime.now(timezone.utc).isoformat()
    refs, facts, skipped = [], [], []
    for r in rows:
        if r["facility_id"] not in active:
            skipped.append({"facility_id": r["facility_id"], "reason": "not an active registered facility"})
            continue
        rh, v = _row_hash(r), r["value"].strip()
        refs.append((rh, SOURCE, r["issue"], r["evidence"], r["retrieved_date"], r["field"], "monitor",
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
                          [(SOURCE, SOURCE, SYNTHETIC_SOURCES[SOURCE]["name"], SOURCE, "monitor_review", None, "active")])
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
    from .registry import load_yaml
    from .warehouse import open_warehouse, SqliteWarehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.monitor_fix")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    ap.add_argument("--check", action="store_true", help="validate the file and exit")
    ap.add_argument("--dry-run", action="store_true", help="report what would be written; write nothing")
    args = ap.parse_args(argv)
    bad = problems()
    if args.check or bad:
        print("\n".join(bad) or f"{PATH.name}: {len(read()[1])} lines OK")
        return 1 if bad else 0
    wh = (SqliteWarehouse(Path(args.db)) if args.db
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    print(json.dumps(apply(wh, dry_run=args.dry_run), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
