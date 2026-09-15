"""Template fetcher. Copy to pipeline/sources/<source_id>.py and implement fetch() and parse().

Rules: verbatim names and addresses (typos included); blank means blank; never fabricate a
row; record row_position; archive the raw file before parsing it; raise LayoutChanged when the
file is not what the parser expects. Check with: python -m pipeline.sources.check <source_id>
"""
from __future__ import annotations
from pathlib import Path
from ._common import http_get, contract_row, require


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [http_get(source["url"], archive_dir)]


def parse(paths: list[Path], source: dict) -> list[dict]:
    raise NotImplementedError(f"parser for {source['id']} not written")


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
