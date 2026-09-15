"""Layer 1: acquire every active source into a contract CSV.

Deterministic for classes A and B (download / bulk / scripted browser). For sources flagged
`ai_extraction: true` (prose pages, PDFs) the fetch is deterministic and a single extraction
call transcribes the page into the contract schema; the page hash and raw model response are
archived beside the CSV so the extraction can be audited.

Each fetcher lives at pipeline/sources/<source_id>.py and exposes fetch() / parse() / pull()
(see pipeline/sources/_common.py). A source with no fetcher, or whose fetcher cannot produce
rows, fails loudly and is listed in the run record — it is never skipped silently. Check one
source on its own with `python -m pipeline.sources.check <source_id> [--file <downloaded file>]`.
"""
from __future__ import annotations
import importlib, json
from datetime import date
from pathlib import Path
from .contract import COLUMNS, write_rows
from . import archive as _archive


class SourceNotImplemented(Exception):
    pass


class SourceFailed(Exception):
    """A fetcher exists but could not produce rows: layout changed, needs a browser, network error."""


def pull_source(source: dict, cfg: dict, out_dir: Path, archive_dir: Path) -> Path:
    sid = source["id"]
    try:
        mod = importlib.import_module(f"pipeline.sources.{sid}")
    except ModuleNotFoundError as e:
        raise SourceNotImplemented(f"{sid}: no fetcher at pipeline/sources/{sid}.py") from e
    try:
        rows = mod.pull(source, cfg, archive_dir / sid / date.today().isoformat())
    except SourceNotImplemented:
        raise
    except Exception as e:  # LayoutChanged, NeedsBrowser, HTTP/URL errors — recorded per source, halts Layer 1 loudly
        raise SourceFailed(f"{sid}: {type(e).__name__}: {e}") from e
    for r in rows:
        r.setdefault("source_id", sid)
        r.setdefault("retrieved_date", date.today().isoformat())
        r.setdefault("country", "US")
        r.setdefault("status_basis", source.get("status_basis", "none"))
        for c in COLUMNS:
            r.setdefault(c, "")
    out = out_dir / f"{sid}.csv"
    write_rows(out, rows, COLUMNS)
    pull = {"source_id": sid, "rows": len(rows), "retrieved": date.today().isoformat(),
            "method": source.get("method"), "ai_extraction": bool(source.get("ai_extraction"))}
    arch = _archive.open_archive(cfg)
    day_dir = archive_dir / sid / date.today().isoformat()
    if arch is not None and day_dir.exists():
        try:
            pull["archive"] = arch.archive_dir(sid, day_dir)
        except _archive.ArchiveError as e:
            raise SourceFailed(f"{sid}: raw archive upload failed — {e}") from e
    else:
        pull["archive"] = {"engine": "none", "note": "BLOB_READ_WRITE_TOKEN not set — raw files stay on this machine only"}
    (out_dir / f"{sid}.pull.json").write_text(json.dumps(pull, indent=2))
    return out
