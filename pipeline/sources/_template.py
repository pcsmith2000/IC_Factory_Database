"""Template fetcher. Copy to pipeline/sources/<source_id>.py and implement pull().

Rules: verbatim names and addresses (typos included); blank means blank; never fabricate a
row; record row_position; archive the raw file before parsing it.
"""
from __future__ import annotations
from pathlib import Path


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    raise NotImplementedError(f"fetcher for {source['id']} not written")
