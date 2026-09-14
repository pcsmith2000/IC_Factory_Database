"""Layer 2: validate each contract CSV, add normalised columns, check drift vs the last pull."""
from __future__ import annotations
import json
from pathlib import Path
from .contract import read_contract, validate_rows, normalise, write_rows, ValidationError


def drift_check(sid: str, n_rows: int, last_pull: Path, tolerance: float) -> str | None:
    if not last_pull.exists():
        return None
    last = json.loads(last_pull.read_text()).get("rows")
    if not last:
        return None
    d = abs(n_rows - last) / last
    if d > tolerance:
        return f"{sid}: row count {n_rows} vs last pull {last} (drift {d:.0%} > {tolerance:.0%}) — source changed shape; halting this source"
    return None


def run(csv_dir: Path, out_dir: Path, last_run_dir: Path | None, tolerance: float) -> dict:
    report = {"sources": {}, "halted": [], "problems": {}}
    for path in sorted(csv_dir.glob("*.csv")):
        sid = path.stem
        rows = read_contract(path)
        problems = validate_rows(sid, rows)
        if problems:
            report["problems"][sid] = problems
            report["halted"].append(sid)
            continue
        if last_run_dir:
            msg = drift_check(sid, len(rows), last_run_dir / f"{sid}.pull.json", tolerance)
            if msg:
                report["halted"].append(sid); report["problems"][sid] = [msg]; continue
        norm = normalise(rows)
        write_rows(out_dir / f"{sid}.csv", norm)
        report["sources"][sid] = {"rows": len(norm), "with_street_key": sum(1 for r in norm if r["street_key"]),
                                  "no_fixed_plant": sum(1 for r in norm if r["no_fixed_plant"])}
    return report
