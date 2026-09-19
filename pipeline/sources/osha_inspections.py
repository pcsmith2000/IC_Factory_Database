"""osha_inspections — see pipeline/sources/_evidence_csv.py for the shared schema, the README this
source's rules come from, and why an erector is not published as a factory."""
from __future__ import annotations
from pathlib import Path
from . import _evidence_csv


def parse(paths: list[Path], source: dict) -> list[dict]:
    return _evidence_csv.read(paths, source)


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    raise NotImplementedError("osha_inspections is blob-only: upload the evidence CSV, then re-run")
