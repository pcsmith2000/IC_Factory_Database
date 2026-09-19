"""ic_directories_more — the second 2026-09-18 evidence collection: 26 programmes in one file.

Same schema and same reader as `ic_directories` (see pipeline/sources/_evidence_csv.py), with two
differences that matter:

  * Its ic_scope is NOT triaged. 4,124 of 5,343 rows read needs_review, needs_role_review,
    fabrication_scope_review or needs_product_review — the collector shipped them deliberately
    unreviewed. That is a request for the classifier, so this source is needs_classify with
    classify_all.
  * The folder holds the 27 per-source files AND the combined one, and the combined file IS the
    other 27. Reading both would count every row twice, so when a combined `*_all_*.csv` is
    present it is the only file read.
"""
from __future__ import annotations
from pathlib import Path
from . import _evidence_csv


def parse(paths: list[Path], source: dict) -> list[dict]:
    combined = [p for p in paths if p.suffix.lower() == ".csv" and "_all_" in p.name.lower()]
    return _evidence_csv.read(combined or paths, source)


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    raise NotImplementedError("ic_directories_more is blob-only: upload the evidence CSV, then re-run")
