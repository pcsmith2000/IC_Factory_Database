"""One row per run: what it cost, how long it took, what it read, and what it scored.

    python -m pipeline.runlog                 # rebuild run_records/RUNLOG.csv from every record
    python -m pipeline.runlog --print         # and print it

Every figure is read out of the run records; nothing here recomputes or estimates. A column is
blank when the record does not carry the field, which is the honest rendering for the runs made
before token accounting was wired into Layer 3 on 2026-09-17 — those runs really did not measure
what they spent, and writing a guess into the column would make the history look better than it is.

`sources` is the list Layer 1 actually acquired, which is not the registry's active list: a source
can be registered and active and still contribute nothing, and that difference is the point of
keeping the column.
"""
from __future__ import annotations
import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "run_records"
OUT = RECORDS / "RUNLOG.csv"

COLUMNS = ["run", "started", "minutes", "ai", "model", "prompt", "sources", "n_sources",
           "input_tokens", "output_tokens", "batches_called", "batches_cached", "classify_minutes",
           "candidates", "ic", "uncertain", "raw_clusters", "published", "located", "t0_leads",
           "recall", "recall_located", "recall_sealed", "control_rows", "ceiling",
           "ingested_but_lost", "never_ingested", "gates", "halted_at"]


def _minutes(started: str | None, finished: str | None) -> str:
    if not (started and finished):
        return ""
    try:
        a = datetime.fromisoformat(started)
        b = datetime.fromisoformat(finished)
    except ValueError:
        return ""
    return f"{(b - a).total_seconds() / 60:.1f}"


def row_for(path: Path) -> dict:
    d = json.loads(path.read_text())
    layers = d.get("layers") or {}
    rel = d.get("release") or {}
    cls = layers.get("3_classify") or {}
    rc = ((layers.get("7_measure") or {}).get("recall")) or {}
    acq = layers.get("1_acquire") or {}
    # Layer 1 records `pulled` as absolute paths to the normalised CSV it wrote per source
    # (".../ic-csv/epa_frs.csv"), so the source id is the file stem. Reading the registry instead
    # would list what was CONFIGURED, and a source can be active and still contribute nothing —
    # which is exactly the difference this column exists to show.
    pulled = acq.get("pulled") if isinstance(acq, dict) else None
    sources = sorted({Path(x).stem for x in pulled if isinstance(x, str)}) if pulled else []
    gates = d.get("gates") or []
    pct = lambda v: f"{v:.3f}" if isinstance(v, (int, float)) else ""
    return {
        "run": path.stem,
        "started": d.get("started", ""),
        "minutes": _minutes(d.get("started"), rel.get("finished")),
        "ai": (rel.get("ai") or "").split(" —")[0],
        "model": cls.get("model", ""),
        "prompt": cls.get("prompt_hash", ""),
        "sources": " ".join(sources),
        "n_sources": len(sources) or "",
        "input_tokens": cls.get("input_tokens", ""),
        "output_tokens": cls.get("output_tokens", ""),
        "batches_called": cls.get("batches_called", ""),
        "batches_cached": cls.get("batches_cached", ""),
        "classify_minutes": (f"{cls['classify_seconds'] / 60:.1f}" if cls.get("classify_seconds") else ""),
        "candidates": cls.get("n_candidates", ""),
        "ic": cls.get("ic", ""),
        "uncertain": cls.get("uncertain", ""),
        "raw_clusters": rel.get("raw_count", ""),
        "published": rel.get("published_count", ""),
        "located": rel.get("located_count", ""),
        "t0_leads": rel.get("t0_leads", ""),
        "recall": pct(rc.get("recall")),
        "recall_located": pct(rc.get("recall_located")),
        "recall_sealed": pct(((rc.get("sealed") or {}).get("recall"))),
        "control_rows": rc.get("in_scope", ""),
        # The recall this database could reach if every ingested-but-lost plant were recovered and
        # nothing else changed. Blank for runs made before Layer 7 could tell the two apart — those
        # runs really did report "not in any source we hold" without checking, and back-filling a
        # number here would hide that.
        "ceiling": pct((gap := (rc.get("source_gap") or {})).get("ceiling")),
        "ingested_but_lost": gap.get("ingested_but_lost", ""),
        "never_ingested": gap.get("never_ingested", ""),
        "gates": " ".join(f"{g['gate'].split()[0]}{'+' if g.get('passed') else '-'}" for g in gates),
        "halted_at": d.get("halted_at") or "",
    }


def build() -> list[dict]:
    rows = [row_for(p) for p in sorted(RECORDS.glob("*.json"))]
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.runlog")
    ap.add_argument("--print", action="store_true", help="print the log after rebuilding it")
    a = ap.parse_args(argv)
    rows = build()
    print(f"{OUT.relative_to(ROOT)}: {len(rows)} runs")
    if a.print:
        keep = ["run", "minutes", "ai", "model", "input_tokens", "published", "recall",
                "recall_located", "recall_sealed", "ceiling", "gates"]
        widths = {k: max(len(k), *(len(str(r.get(k, ""))) for r in rows)) for k in keep} if rows else {}
        print("  " + "  ".join(k.ljust(widths[k]) for k in keep))
        for r in rows:
            print("  " + "  ".join(str(r.get(k, "")).ljust(widths[k]) for k in keep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
