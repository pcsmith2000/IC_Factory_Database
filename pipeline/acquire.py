"""Layer 1: acquire every active source into a contract CSV.

Deterministic for classes A and B (download / bulk / scripted browser). For sources flagged
`ai_extraction: true` (prose pages, PDFs) the fetch is deterministic and a single extraction
call transcribes the page into the contract schema; the page hash and raw model response are
archived beside the CSV so the extraction can be audited.

The bytes come from one of two places (registry `acquire:` per source, else `archive.mode`):
  web-first  fetch the site, archive what came back; on failure fall back to the newest archived
             copy so one 500 or a new WAF rule does not lose a quarterly run
  blob-only  never touch the site — parse the newest copy in the archive. This is how a source
             that publishes no downloadable list (MA, NY, MI) or blocks automation enters the
             pipeline: put the file in the store under ic-sources/<source_id>/<date>/ and re-run.

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
from inspect import signature as _sig_of
from . import archive as _archive
from . import ai_enabled


def _sig(fn):
    return list(_sig_of(fn).parameters)


class SourceNotImplemented(Exception):
    pass


class SourceFailed(Exception):
    """A fetcher exists but could not produce rows: layout changed, needs a browser, network error."""


class SourceSkipped(Exception):
    """Deliberately not pulled this run (IC_AI=off vs. an `ai_extraction` source). Not a failure."""


def pull_source(source: dict, cfg: dict, out_dir: Path, archive_dir: Path) -> Path:
    sid = source["id"]
    if source.get("ai_extraction") and not ai_enabled():
        raise SourceSkipped(f"{sid}: ai_extraction source and IC_AI=off — no rows from this source this run")
    try:
        mod = importlib.import_module(f"pipeline.sources.{sid}")
    except ModuleNotFoundError as e:
        raise SourceNotImplemented(f"{sid}: no fetcher at pipeline/sources/{sid}.py") from e
    arch = _archive.open_archive(cfg)
    day_dir = archive_dir / sid / date.today().isoformat()
    mode = source.get("acquire") or (cfg.get("archive") or {}).get("mode", "web-first")
    note, archived = "", None

    def from_blob(why: str):
        """Parse the newest copy held in the archive instead of going to the web."""
        if arch is None:
            raise SourceFailed(f"{sid}: {why}, and no archive is configured (BLOB_READ_WRITE_TOKEN unset)")
        dates = arch.dates_for(sid)
        if not dates:
            raise SourceFailed(f"{sid}: {why}, and the archive holds no copy under "
                               f"{arch.prefix}/{sid}/<date>/ — upload the file there, then re-run")
        d = dates[0]
        files = arch.fetch_folder(sid, d, archive_dir / sid / d)
        if not files:
            raise SourceFailed(f"{sid}: archive folder {d} is empty")
        return mod.parse(files, source), f"parsed from the archived copy of {d} ({why})"

    try:
        if mode == "blob-only":
            rows, note = from_blob("this source is not fetched automatically")
        else:
            try:
                # Download, store, then parse the stored bytes' local copy. Archiving before parsing
                # is deliberate: when a layout changes, the file that broke the parser is already in
                # the store to inspect, instead of being lost with the exception.
                files = mod.fetch(source, cfg, day_dir)
                if arch is not None:
                    archived = arch.archive_dir(sid, day_dir)
                rows = mod.parse(files, source) if len(_sig(mod.parse)) == 2 else mod.parse(files, source, cfg)
            except SourceNotImplemented:
                raise
            except Exception as e:
                if mode == "web-first" and arch is not None and arch.dates_for(sid):
                    rows, note = from_blob(f"live fetch or parse failed — {type(e).__name__}: {str(e)[:120]}")
                else:
                    raise
    except SourceNotImplemented:
        raise
    except SourceFailed:
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
            "method": source.get("method"), "ai_extraction": bool(source.get("ai_extraction")),
            "acquire_mode": mode, "source_of_bytes": note or "live fetch"}
    if archived is not None:
        pull["archive"] = archived
    elif note:
        pull["archive"] = {"engine": "none", "note": "bytes came from the archive; nothing new to store"}
    else:
        pull["archive"] = {"engine": "none", "note": "BLOB_READ_WRITE_TOKEN not set — raw files stay on this machine only"}
    (out_dir / f"{sid}.pull.json").write_text(json.dumps(pull, indent=2))
    return out
