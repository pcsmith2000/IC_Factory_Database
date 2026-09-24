"""Check the hand-placed inputs before a run, and keep the control checksum honest.

    python -m pipeline.control check          # validate every control file against the contract and config
    python -m pipeline.control check --fix    # also: fill blank seed row_hash values, rewrite control.sha256

Files (all under control/, all committed):
  control-triaged.csv      the held-out control list: control_id,name,city,state,triage,reason
  seeds.csv                contract columns + seed_label (IC / NOT-IC); hidden in every classifier batch for G5
  frame_state_totals.csv   state,establishments — Census CBP state totals for the core NAICS codes (Layer 7 denominator)
  crosswalk.csv            control_id,row_hash,facility_id,legal_entity_id — explicit links (optional)
  operator_assertions.csv  facility_id,field,value,retrieved_date,note — human corrections (optional)
Nothing here edits the data; --fix only derives row_hash from verbatim columns and recomputes the checksum.
"""
from __future__ import annotations
import argparse, csv, re, sys
from pathlib import Path
from .contract import COLUMNS, row_hash, STATUS_BASES
from .registry import load_yaml, sha256_file
from .golden import FIELD_MAP

ROOT = Path(__file__).resolve().parent.parent
TRIAGE = {"in_scope_locatable", "in_scope_no_location", "out_of_scope"}
STATE = re.compile(r"^[A-Z]{2}$")


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return list(r.fieldnames or []), list(r)


def check(cfg: dict, fix: bool = False) -> list[str]:
    problems: list[str] = []
    c = ROOT / "control"

    # ---- control-triaged.csv
    p = ROOT / cfg["control"]["path"]
    hdr, rows = _read(p)
    need = ["control_id", "name", "city", "state", "triage", "reason", "split"]
    if hdr[:7] != need:
        problems.append(f"{p.name}: columns must be {need}, got {hdr}")
    ids = [r["control_id"] for r in rows]
    if len(ids) != len(set(ids)):
        problems.append(f"{p.name}: duplicate control_id values")
    for i, r in enumerate(rows, 2):
        # Blank triage is allowed and is NOT the same as a bad value: Layer 7 treats an untriaged
        # row as in scope and reports how many it assumed, so a bare verified list scores recall
        # the day it lands. Rejecting blanks here would have forced a triage nobody has done yet,
        # and the only way to satisfy it quickly is to invent one.
        if (r.get("triage") or "").strip() and r["triage"].strip() not in TRIAGE:
            problems.append(f"{p.name} line {i}: triage {r.get('triage')!r} not in {sorted(TRIAGE)} (blank = untriaged, allowed)")
        if not (r.get("name") or "").strip():
            problems.append(f"{p.name} line {i}: blank name")
        if r.get("state") and not STATE.match(r["state"].strip().upper()):
            problems.append(f"{p.name} line {i}: state {r['state']!r} is not a 2-letter code")
    for i, r in enumerate(rows, 2):
        if (r.get("split") or "").strip() not in {"dev", "sealed"}:
            problems.append(f"{p.name} line {i}: split {r.get('split')!r} must be dev or sealed")
    in_scope = sum(1 for r in rows if not (r.get("triage") or "").strip()
                   or r["triage"].strip().startswith("in_scope"))
    untriaged = sum(1 for r in rows if not (r.get("triage") or "").strip())
    exp_total, exp_in = cfg["control"].get("total_rows"), cfg["control"].get("in_scope_rows")
    if rows and exp_total and len(rows) != exp_total:
        problems.append(f"{p.name}: {len(rows)} rows but registry/config.yaml control.total_rows = {exp_total} — update one of them in the same commit")
    if rows and exp_in and in_scope != exp_in:
        problems.append(f"{p.name}: {in_scope} in-scope rows but config control.in_scope_rows = {exp_in}")
    if untriaged:
        # A note, not a problem: an untriaged row is measurable, just measured against a wider
        # denominator. Putting it in `problems` would fail the release for the absence of an
        # opinion rather than the absence of data.
        print(f"  note: {p.name}: {untriaged} of {len(rows)} rows untriaged — Layer 7 counts them "
              f"in scope and reports that it did", file=sys.stderr)
    if not rows:
        problems.append(f"{p.name}: EMPTY — G4 passes on headers alone but Layer 7 recall will be 0/0; place the 241-row triaged list")
    # checksum
    sha_path = ROOT / cfg["control"]["checksum_path"]
    actual = sha256_file(p)
    recorded = sha_path.read_text().split()[0] if sha_path.exists() else None
    if recorded != actual:
        if fix:
            sha_path.write_text(f"{actual}  {p.name}\n"); print(f"  wrote {sha_path.name} = {actual[:12]}…")
        else:
            problems.append(f"{sha_path.name}: recorded {str(recorded)[:12]}… but {p.name} hashes to {actual[:12]}… (run with --fix, in the same commit as the edit)")

    # ---- seeds.csv
    p = c / "seeds.csv"
    hdr, rows = _read(p)
    missing = [x for x in COLUMNS + ["row_hash", "seed_label"] if x not in hdr]
    if missing:
        problems.append(f"seeds.csv: missing columns {missing}")
    labels = {"IC": 0, "NOT-IC": 0}
    changed = False
    for i, r in enumerate(rows, 2):
        if r.get("seed_label") not in labels:
            problems.append(f"seeds.csv line {i}: seed_label {r.get('seed_label')!r} must be IC or NOT-IC"); continue
        labels[r["seed_label"]] += 1
        for col in ("source_id", "name_verbatim", "state_verbatim"):
            if not (r.get(col) or "").strip():
                problems.append(f"seeds.csv line {i}: blank {col}")
        if r.get("status_basis") not in STATUS_BASES:
            problems.append(f"seeds.csv line {i}: status_basis {r.get('status_basis')!r} not in {sorted(STATUS_BASES)}")
        want = row_hash(r)
        if r.get("row_hash") != want:
            if fix:
                r["row_hash"] = want; changed = True
            else:
                problems.append(f"seeds.csv line {i}: row_hash {r.get('row_hash')!r} ≠ hash of the verbatim columns ({want}); --fix derives it")
    if changed:
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=hdr); w.writeheader(); w.writerows(rows)
        print(f"  rewrote seeds.csv row_hash for {sum(1 for _ in rows)} rows")
    sp, sn = cfg["classifier"].get("seeded_positives"), cfg["classifier"].get("seeded_negatives")
    if rows and (labels["IC"] < sp or labels["NOT-IC"] < sn):
        problems.append(f"seeds.csv: {labels['IC']} IC / {labels['NOT-IC']} NOT-IC seeds; config asks for ≥{sp} / ≥{sn}")
    if not rows:
        problems.append("seeds.csv: EMPTY — G5 fails any run that classifies (epa_frs is active), by design; place the ~60 seeds")

    # ---- frame_state_totals.csv
    p = c / "frame_state_totals.csv"
    hdr, rows = _read(p)
    if hdr[:2] != ["state", "establishments"]:
        problems.append(f"frame_state_totals.csv: columns must be state,establishments, got {hdr}")
    total = 0
    for i, r in enumerate(rows, 2):
        if not STATE.match((r.get("state") or "").strip().upper()):
            problems.append(f"frame_state_totals.csv line {i}: state {r.get('state')!r}")
        try:
            total += int(r["establishments"])
        except (KeyError, ValueError):
            problems.append(f"frame_state_totals.csv line {i}: establishments must be an integer")
    floor = cfg["frame"].get("establishments_total")
    if rows and floor and total < floor:
        problems.append(f"frame_state_totals.csv: sums to {total}, below config frame.establishments_total = {floor} (a floor)")
    if not rows:
        problems.append(f"frame_state_totals.csv: EMPTY — Layer 7 coverage/bias will be reported as empty; place Census CBP {cfg['frame'].get('vintage')} state totals for {cfg['frame'].get('naics')}")

    # ---- crosswalk.csv, operator_assertions.csv (optional content, fixed columns)
    hdr, rows = _read(c / "crosswalk.csv")
    if hdr[:4] != ["control_id", "row_hash", "facility_id", "legal_entity_id"]:
        problems.append(f"crosswalk.csv: columns must be control_id,row_hash,facility_id,legal_entity_id, got {hdr}")
    hdr, rows = _read(c / "operator_assertions.csv")
    if hdr[:5] != ["facility_id", "field", "value", "retrieved_date", "note"]:
        problems.append(f"operator_assertions.csv: columns must be facility_id,field,value,retrieved_date,note, got {hdr}")
    golden_fields = set(FIELD_MAP) | {"lat_lon", "legal_name", "product_type", "existence_flag"}
    for i, r in enumerate(rows, 2):
        if r.get("field") not in golden_fields:
            problems.append(f"operator_assertions.csv line {i}: field {r.get('field')!r} not a golden field {sorted(golden_fields)}")
        # existence_flag from a person is a decision, so it takes one of two words: not_ic takes the
        # facility out of golden, review puts it back in the queue. A typo must not silently do neither.
        if r.get("field") == "existence_flag" and r.get("value") not in ("not_ic", "review"):
            problems.append(f"operator_assertions.csv line {i}: existence_flag must be not_ic or review, got {r.get('value')!r}")
        if not re.match(r"^IC-\d{5}$", r.get("facility_id") or ""):
            problems.append(f"operator_assertions.csv line {i}: facility_id {r.get('facility_id')!r} is not an IC-number")

    # ---- prompt
    prompt = (ROOT / cfg["classifier"]["prompt_path"]).read_text()
    if "placeholder" in prompt.lower():
        problems.append(f"{cfg['classifier']['prompt_path']}: still the v1.0 placeholder — paste the frozen 2026-09-09 prompt (G5 re-scores it)")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.control")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check"); ck.add_argument("--fix", action="store_true", help="derive seed row_hash values and rewrite control.sha256")
    args = ap.parse_args(argv)
    cfg = load_yaml(ROOT / "registry" / "config.yaml")
    problems = check(cfg, fix=args.fix)
    for p in problems:
        print("  PROBLEM", p)
    print(f"control check: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
