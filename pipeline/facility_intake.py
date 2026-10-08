"""New facilities by pull request: a plant no source lists yet reaches the registry and golden.

Every other path into the warehouse starts from a source row (a registry, a directory, a list) and
reconcile gives it an IC-number. A plant ADL knows about that no source lists (a new factory, one
found by research or on a site visit) had no stable way in: ADL_Viz's chat could create one, and a
created plant left golden at the next full run, because golden keeps only plants the current release
asserts something about. This is the reviewed way in:

    control/new_facilities.csv, one line per plant:
    intake_id,name,address,city,state,zip,website,phone,capability_group,capability_leaf,sq_ft,lat_lon,
    retrieved_date,issue,evidence,proposed_by,not_duplicate_of

`intake_id` names the line forever (NF-<slug>, e.g. NF-custom-touch-homes-madison-sd). An agent or a
person adds a line in a pull request; a person merges it; on main, `apply`:

  * refuses a line that looks like a plant golden already holds (same name in the state, or the same
    street address in the city), unless `not_duplicate_of` lists every such IC-number: the proposer
    looked and says they are different plants;
  * mints one permanent IC-number from the registry's sequence and records the intake_id as the
    plant's match key `intake|<intake_id>`, so a re-run finds the same plant and never mints twice;
  * appends each non-blank field as source `facility_intake` (class `facility_intake`) under the
    current release. The fact_assertions trigger queues the plant and golden-refresh brings it in.

A founding fact fills a blank and keeps the plant in golden across releases (golden_refresh
FOUNDING_SOURCES); it ranks below every registry and list (registry/survivorship.yaml leaves it
unlisted), so the first real source to list the plant corrects it. The capability it names outranks
the model's guess. People outrank everything, as always: to rule the plant not IC, closed, or to fix
a field, use control/operator_assertions.csv with its IC-number.

    python -m pipeline.facility_intake --check       # validate the file, touch nothing
    python -m pipeline.facility_intake [--dry-run]   # mint and append to the warehouse
"""
from __future__ import annotations
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .monitor_fix import _h

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "control" / "new_facilities.csv"
TAXONOMY = ROOT / "registry" / "taxonomy.yaml"
SOURCE = "facility_intake"
COLUMNS = ["intake_id", "name", "address", "city", "state", "zip", "website", "phone", "capability_group",
           "capability_leaf", "sq_ft", "lat_lon", "retrieved_date", "issue", "evidence", "proposed_by",
           "not_duplicate_of"]
# The columns that become golden facts, in the order they are asserted.
FIELDS = ("name", "address", "city", "state", "zip", "website", "phone", "capability_group", "capability_leaf",
          "sq_ft", "lat_lon")
REQUIRED = ("intake_id", "name", "address", "city", "state", "capability_group", "capability_leaf",
            "retrieved_date", "issue", "evidence", "proposed_by")
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
             "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU AS MP".split())
_VALUE = {
    "intake_id": re.compile(r"^NF-[a-z0-9]+(-[a-z0-9]+)*$"),
    "zip": re.compile(r"^\d{5}(-\d{4})?$"),
    "website": re.compile(r"^https?://\S+$"),
    "sq_ft": re.compile(r"^[1-9]\d*$"),
    "retrieved_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "issue": re.compile(r"^(#\d+|https://github\.com/\S+/(issues|pull)/\d+)$"),
}
_IC = re.compile(r"^IC-\d{5}$")


def _norm(s: str | None) -> str:
    """Lower case, words only, common suffixes and street words reduced: for duplicate checks, not storage."""
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    swap = {"street": "st", "road": "rd", "avenue": "ave", "highway": "hwy", "drive": "dr", "boulevard": "blvd",
            "lane": "ln", "north": "n", "south": "s", "east": "e", "west": "w", "parkway": "pkwy", "court": "ct"}
    drop = {"inc", "llc", "corp", "corporation", "co", "company", "ltd", "the", "of"}
    return " ".join(swap.get(w, w) for w in s.split() if w not in drop)


def read(path: Path = PATH) -> tuple[list[str], list[dict]]:
    if not path.exists():
        return COLUMNS, []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), [{k: (v or "").strip() for k, v in row.items()} for row in r]


def _leaf_group() -> dict[str, str]:
    from .registry import load_yaml
    tax = load_yaml(TAXONOMY)
    return {leaf["name"]: g["name"] for g in tax["groups"] for leaf in g["leaves"]}


def problems(path: Path = PATH) -> list[str]:
    hdr, rows = read(path)
    if hdr != COLUMNS:
        return [f"{path.name}: columns must be {','.join(COLUMNS)}, got {hdr}"]
    out, seen = [], {}
    leaf_group = _leaf_group()
    for i, r in enumerate(rows, 2):
        where = f"{path.name} line {i}"
        for c in REQUIRED:
            if not r.get(c):
                out.append(f"{where}: {c} is required")
        for c, pat in _VALUE.items():
            if r.get(c) and not pat.match(r[c]):
                out.append(f"{where}: {c} {r[c]!r} is not valid")
        if r.get("intake_id") in seen:
            out.append(f"{where}: intake_id {r['intake_id']} repeats line {seen[r['intake_id']]}; one line per plant")
        seen.setdefault(r.get("intake_id"), i)
        if r.get("state") and r["state"] not in STATES:
            out.append(f"{where}: state {r['state']!r} is not a two-letter US state code")
        if r.get("capability_leaf") and r["capability_leaf"] not in leaf_group:
            out.append(f"{where}: capability_leaf {r['capability_leaf']!r} is not a leaf of registry/taxonomy.yaml")
        elif r.get("capability_leaf") and r.get("capability_group") != leaf_group[r["capability_leaf"]]:
            out.append(f"{where}: capability_leaf {r['capability_leaf']!r} belongs to group "
                       f"{leaf_group[r['capability_leaf']]!r}, not {r.get('capability_group')!r}")
        if r.get("phone") and not 10 <= len(re.sub(r"\D", "", r["phone"])) <= 11:
            out.append(f"{where}: phone {r['phone']!r} needs a 10-digit US number")
        if r.get("lat_lon"):
            try:
                lat, lon = (float(x) for x in r["lat_lon"].split(","))
                if not (17 <= lat <= 72 and -180 <= lon <= -60):
                    raise ValueError
            except ValueError:
                out.append(f"{where}: lat_lon {r['lat_lon']!r} must be latitude,longitude in the United States")
        if r.get("evidence") and (len(r["evidence"]) < 20 or not re.search(r"https?://", r["evidence"])):
            out.append(f"{where}: evidence must cite the page(s) that show the plant exists there (a URL) and say what they show")
        for x in filter(None, (s.strip() for s in r.get("not_duplicate_of", "").split(";"))):
            if not _IC.match(x):
                out.append(f"{where}: not_duplicate_of {x!r} is not an IC-number (separate several with ';')")
    return out


def _row_hash(r: dict) -> str:
    return _h(SOURCE, r["intake_id"], r["retrieved_date"])


def look_alikes(r: dict, golden: list[dict]) -> list[str]:
    """IC-numbers of golden plants this line may duplicate: the same name in the state, or the same
    street address in the city and state."""
    name, addr, city = _norm(r["name"]), _norm(r["address"]), _norm(r["city"])
    hits = []
    for g in golden:
        if (g.get("state") or "").upper() != r["state"]:
            continue
        if name and _norm(g.get("name")) == name:
            hits.append(g["facility_key"])
        elif addr and _norm(g.get("address")) == addr and _norm(g.get("city")) == city:
            hits.append(g["facility_key"])
    return sorted(set(hits))


def apply(wh, *, path: Path = PATH, dry_run: bool = False) -> dict:
    from .facility_registry import _draw, _event
    from .golden_refresh import current_release
    from .warehouse import SYNTHETIC_SOURCES, _date_row
    bad = problems(path)
    if bad:
        raise ValueError("; ".join(bad))
    tag = current_release(wh)
    _, rows = read(path)
    keys = {r["match_key"]: r["facility_id"] for r in
            wh.query("SELECT match_key, facility_id FROM facility_match_key WHERE match_key LIKE ?", ("intake|%",))}
    status = {r["facility_id"]: r["status"] for r in wh.query("SELECT facility_id, status FROM facility")}
    golden = wh.query("SELECT facility_key, name, address, city, state FROM golden_facility")
    now = datetime.now(timezone.utc).isoformat()
    report = {"release_tag": tag, "lines": len(rows), "minted": [], "existing": [], "refused": [], "skipped": [],
              "written": 0, "already_present": 0, "dry_run": dry_run}
    for r in rows:
        key = f"intake|{r['intake_id']}"
        fid = keys.get(key)
        if fid and status.get(fid) != "active":
            report["skipped"].append({"intake_id": r["intake_id"], "facility_id": fid,
                                      "reason": f"its plant is {status.get(fid)}, not active"})
            continue
        if not fid:
            cleared = {x.strip() for x in r["not_duplicate_of"].split(";") if x.strip()}
            alike = [k for k in look_alikes(r, golden) if k not in cleared]
            if alike:
                report["refused"].append({"intake_id": r["intake_id"], "looks_like": alike,
                                          "reason": "golden already holds a plant with this name in the state or this "
                                                    "address in the city; merge the facts onto it, or list it in "
                                                    "not_duplicate_of if it is a different plant"})
                continue
        rh = _row_hash(r)
        facts = []
        for f in FIELDS:
            if r.get(f):
                facts.append((f, r[f]))
        if dry_run and not fid:
            report["minted"].append({"intake_id": r["intake_id"], "facility_id": None, "fields": [f for f, _ in facts]})
            report["written"] += len(facts)
            continue
        with wh.transaction() as c:
            if not fid:
                fid = _draw(wh, c)
                c.execute("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                          "VALUES (?, 'active', NULL, ?, ?)", (fid, now, f"facility_intake:{r['proposed_by']}"))
                c.execute("INSERT INTO facility_match_key (match_key, facility_id, method, confidence, source, first_seen) "
                          "VALUES (?, ?, 'intake', 1.0, 'control/new_facilities.csv', ?)", (key, fid, now))
                _event(c, "mint", fid, f"facility_intake:{r['proposed_by']}",
                       f"new facility {r['intake_id']} ({r['issue']})", match_key=key, at=now)
                keys[key], status[fid] = fid, "active"
                golden.append({"facility_key": fid, "name": r["name"], "address": r["address"], "city": r["city"],
                               "state": r["state"]})
                report["minted"].append({"intake_id": r["intake_id"], "facility_id": fid, "fields": [f for f, _ in facts]})
            else:
                report["existing"].append({"intake_id": r["intake_id"], "facility_id": fid})
            ids = [_h(SOURCE, "assertion", fid, f, v, r["retrieved_date"]) for f, v in facts]
            have = {x["assertion_id"] for x in wh._rows(c.execute(
                f"SELECT assertion_id FROM fact_assertions WHERE release_tag = ? AND assertion_id IN ({','.join('?' * len(ids))})",
                (tag, *ids)))} if ids else set()
            new = [(aid, tag, fid, SOURCE, f, r["retrieved_date"], v, SOURCE, 0, rh, 1.0, SOURCE, now)
                   for aid, (f, v) in zip(ids, facts) if aid not in have]
            report["already_present"] += len(have)
            report["written"] += len(new)
            if dry_run or not new:
                continue
            c.execute("INSERT INTO dim_source VALUES (?,?,?,?,?,?,?) ON CONFLICT (source_key) DO NOTHING",
                      (SOURCE, SOURCE, SYNTHETIC_SOURCES[SOURCE]["name"], SOURCE, "reviewed_pull_request", None, "active"))
            d = _date_row(r["retrieved_date"])
            if d:
                c.execute("INSERT INTO dim_date VALUES (?,?,?,?) ON CONFLICT (date_key) DO NOTHING", d)
            c.execute("INSERT INTO ref_source_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (row_hash) DO NOTHING",
                      (rh, SOURCE, r["issue"], f"{r['evidence']} (proposed by {r['proposed_by']})", r["retrieved_date"],
                       r["intake_id"], r["intake_id"], r["name"], r["address"], r["city"], r["state"], r["zip"] or None,
                       fid, SOURCE, 1.0, tag))
            c.executemany("INSERT INTO fact_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                          "ON CONFLICT (assertion_id, release_tag) DO NOTHING", new)
    return report


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    from .warehouse import open_warehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.facility_intake")
    ap.add_argument("--check", action="store_true", help="validate control/new_facilities.csv; touch nothing")
    ap.add_argument("--dry-run", action="store_true", help="report what would be minted and written; write nothing")
    args = ap.parse_args(argv)
    bad = problems()
    if args.check:
        print(json.dumps({"ok": not bad, "problems": bad}, indent=1))
        return 1 if bad else 0
    if bad:
        print(json.dumps({"ok": False, "problems": bad}, indent=1), file=sys.stderr)
        return 1
    wh = open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT)
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr)
        return 1
    try:
        out = apply(wh, dry_run=args.dry_run)
    finally:
        wh.close()
    print(json.dumps(out, indent=1))
    return 1 if out["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
