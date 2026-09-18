"""adl_4ward — one of ADL's own plant lists. See pipeline/sources/_adl_list.py for the shared reader,
the values it refuses, and why the control test was retired when these became sources.
"""
from __future__ import annotations
from pathlib import Path
from . import _adl_list
from ..registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent.parent


def _drop_names(source: dict) -> set[str]:
    """Names the registry says are not manufacturers, lower-cased."""
    return {n.strip().lower() for n in (source.get("not_manufacturers") or []) if n.strip()}


def parse(paths: list[Path], source: dict) -> list[dict]:
    return _adl_list.read(paths, source, drop_names=_drop_names(source))


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    raise NotImplementedError("adl_4ward is blob-only: upload the transcribed list, then re-run")
