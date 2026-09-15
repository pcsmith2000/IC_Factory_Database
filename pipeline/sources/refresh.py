"""Populate the archive from the public web. This is SETUP, not part of a run.

The quarterly run reads whatever is in the Blob store and never scrapes (`archive.mode: blob-only`),
so a site going down, changing layout, or adding a WAF rule can never take a run with it. Getting
files into the store is this command, run deliberately by a person:

    python -m pipeline.sources.refresh --all           # every source with a working fetcher
    python -m pipeline.sources.refresh tx_tdlr iibc    # named sources
    python -m pipeline.sources.refresh --list          # what the store already holds, per source

It downloads, uploads to `ic-sources/<source_id>/<date>/`, and reports. It does not parse and does
not touch ic-csv/ — parsing is the run's job, against the stored bytes. A source that publishes no
downloadable list (mi_lara, ma_bbrs, ny_dos) or needs a browser (or_bcd, fl_bcis) has no fetcher to
run here: obtain the file however it is obtainable and upload it to the same path.
"""
from __future__ import annotations
import argparse, importlib, sys
from datetime import date
from pathlib import Path
from .. import archive as _archive
from ..registry import load_yaml, active_sources
from ..acquire import _sig   # parse() takes (files, source) or (files, source, cfg)

ROOT = Path(__file__).resolve().parent.parent.parent


def refresh_one(source: dict, cfg: dict, arch, cache: Path) -> dict:
    sid = source["id"]
    mod = importlib.import_module(f"pipeline.sources.{sid}")
    day_dir = cache / sid / date.today().isoformat()
    files = mod.fetch(source, cfg, day_dir)
    reduced = _reduce_oversize(mod, files, source, cfg, arch)
    return {"source_id": sid, "files": len(files), "reduced": reduced, **arch.archive_dir(sid, day_dir)}


def _reduce_oversize(mod, files: list[Path], source: dict, cfg: dict, arch) -> str:
    """Replace a payload that is over archive.max_file_mb with the reduced artifact parse() writes.

    archive_dir records an oversize file by hash and does NOT upload it, so archiving the raw
    download would leave the store holding a manifest and nothing the run could read back
    ("archive folder is empty"). EPA's national_combined.zip (1.27 GB) is the case this exists
    for: parse() writes national_combined.filtered.zip beside it — the rows actually used plus
    SOURCE.json carrying the original url, size and sha256 — and that slice parses identically.
    """
    over = [p for p in files if p.exists() and p.stat().st_size > arch.max_bytes]
    if not over:
        return ""
    before = {p.resolve() for p in over[0].parent.rglob("*") if p.is_file()}
    mod.parse(files, source, cfg) if len(_sig(mod.parse)) == 3 else mod.parse(files, source)
    made = sorted(p for p in over[0].parent.rglob("*") if p.is_file() and p.resolve() not in before)
    if not made:
        raise RuntimeError(f"{source['id']}: {over[0].name} is over the {arch.max_bytes >> 20} MB archive cap "
                           f"and parse() wrote no reduced artifact to archive in its place")
    for p in over:
        p.unlink()   # its identity survives in the slice's SOURCE.json and the .meta.json sidecar
    return f"{', '.join(p.name for p in over)} (over cap) -> {', '.join(p.name for p in made)}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.sources.refresh")
    ap.add_argument("source_id", nargs="*")
    ap.add_argument("--all", action="store_true", help="every active source that has a fetcher")
    ap.add_argument("--list", action="store_true", help="show what the store already holds")
    args = ap.parse_args(argv)

    reg, cfg = load_yaml(ROOT / "registry" / "sources.yaml"), load_yaml(ROOT / "registry" / "config.yaml")
    arch = _archive.open_archive({**cfg, "archive": {**(cfg.get("archive") or {}), "mode": "web-first"}})
    if arch is None:
        print("no BLOB_READ_WRITE_TOKEN — nothing to refresh into", file=sys.stderr); return 1

    if args.list:
        for s in active_sources(reg):
            dates = arch.dates_for(s["id"])
            print(f"  {s['id']:<22} {(dates[0] if dates else 'EMPTY — upload a file'):<24} {len(dates)} version(s)")
        return 0

    chosen = [s for s in active_sources(reg) if args.all or s["id"] in args.source_id]
    if not chosen:
        print("name a source, or --all (see --list)", file=sys.stderr); return 1
    bad = 0
    for s in chosen:
        try:
            r = refresh_one(s, cfg, arch, ROOT / cfg["storage"]["local_cache"])
            print(f"  {s['id']:<22} {r['uploaded']} file(s), {r['bytes']} bytes → {r['manifest'].rsplit('/', 2)[0]}/")
        except Exception as e:
            bad += 1
            print(f"  {s['id']:<22} NOT REFRESHED  {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
    print(f"refresh: {len(chosen) - bad}/{len(chosen)} sources updated in the store")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
