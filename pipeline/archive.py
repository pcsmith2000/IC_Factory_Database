"""Raw-source archive: every file Layer 1 fetched, kept off the runner.

Engine today: Vercel Blob, selected when BLOB_READ_WRITE_TOKEN is set (BLOB_STORE_ID optionally
names the store explicitly; otherwise it is read out of the token). Objects are keyed
<prefix>/<source_id>/<date>/<file> (the layout registry/config.yaml has always described), access
private, overwrite allowed (a re-run on the same day replaces the same keys). A manifest.json per
source/date lists every file with size and sha256, including files too large to upload — the
EPA national_combined.zip (1.27 GB) is recorded by hash and skipped; the fetcher archives the
filtered slice it actually used instead (pipeline/sources/epa_frs.py).

The request contract is the one @vercel/blob 2.x `put()` sends (read from the SDK source):
PUT https://vercel.com/api/blob/?pathname=…  with  authorization: Bearer <token>, x-api-version: 12,
x-vercel-blob-access, x-content-type, x-add-random-suffix, x-allow-overwrite, x-vercel-blob-store-id.

No token → no archive, and the run record says so. Token present but an upload fails → the
source fails at Layer 1 (never silently skipped). IC_ARCHIVE=off disables it explicitly.

    python -m pipeline.archive verify     # real round trip: put a probe file, read it back, delete it
    python -m pipeline.archive list <source_id> <date>    # what the manifest for one pull recorded
"""
from __future__ import annotations
import hashlib, json, mimetypes, os, random, sys, time, urllib.parse, urllib.request
from pathlib import Path

BLOB_API = os.environ.get("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob")
BLOB_API_VERSION = "12"


class ArchiveError(Exception):
    pass


class VercelBlobArchive:
    engine = "vercel_blob"

    def __init__(self, token: str, *, prefix: str = "ic-sources", access: str = "private", max_file_mb: int = 100,
                 store_id: str | None = None):
        self.token, self.prefix, self.access, self.max_bytes = token, prefix.strip("/"), access, int(max_file_mb * 1024 * 1024)
        parts = token.split("_")
        # A store id given explicitly (BLOB_STORE_ID) wins over the one encoded in the token, which is
        # only a convention; `store_`-prefixed ids are normalised the way the SDK normalises them.
        sid = (store_id or "").strip() or (parts[3] if len(parts) > 3 else "")
        self.store_id = sid[len("store_"):] if sid.startswith("store_") else sid

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "authorization": f"Bearer {self.token}", "x-api-version": BLOB_API_VERSION,
            "x-vercel-blob-store-id": self.store_id,
            "x-api-blob-request-id": f"{self.store_id}:{int(time.time()*1000)}:{random.random().hex()[2:]}",
            "x-api-blob-request-attempt": "0",
        }
        h.update(extra or {})
        return h

    def _call(self, url: str, *, method: str, what: str, data: bytes | None = None, headers: dict | None = None,
              timeout: int = 600, retries: int = 3):
        """One Blob API call, retrying 5xx, 429 and transport errors the way http_get does.

        Layer 1 halts the whole run when any source fails, so an unretried blip here costs the
        acquisition of every other source too — a single [SSL: UNEXPECTED_EOF_WHILE_READING]
        listing one prefix is enough. A 4xx other than 429 is not retried: it means the request
        itself is wrong. Every method here is idempotent (PUT sends x-allow-overwrite, and the
        rest are reads or a keyed delete), so a retried call cannot double-apply.
        """
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=self._headers(headers))
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if (e.code < 500 and e.code != 429) or attempt == retries:
                    raise ArchiveError(f"blob {what}: HTTP {e.code} {e.read()[:300]!r}") from e
            except urllib.error.URLError as e:
                if attempt == retries:
                    raise ArchiveError(f"blob {what}: {e.reason}") from e
            time.sleep(2 ** attempt)

    def put(self, path: Path, pathname: str) -> dict:
        body = path.read_bytes()
        return self._call(f"{BLOB_API}/?{urllib.parse.urlencode({'pathname': pathname})}", method="PUT", data=body,
                          what=f"put {pathname}", headers={
                              "x-vercel-blob-access": self.access, "x-add-random-suffix": "0", "x-allow-overwrite": "1",
                              "x-content-type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                              "x-content-length": str(len(body))})

    def head(self, url_or_pathname: str) -> dict:
        """Metadata for one blob (pathname, size, url). Raises ArchiveError (HTTP 404) when absent."""
        return self._call(f"{BLOB_API}/?{urllib.parse.urlencode({'url': url_or_pathname})}", method="GET",
                          what=f"head {url_or_pathname}", timeout=60)

    def delete(self, urls: list[str]) -> None:
        self._call(f"{BLOB_API}/delete", method="POST", data=json.dumps({"urls": urls}).encode(),
                   what=f"delete {len(urls)} object(s)", headers={"content-type": "application/json"}, timeout=120)

    # ---- reads: the store as an input, not just a sink
    def list_prefix(self, prefix: str) -> list[dict]:
        """Every blob under a prefix, following the cursor. [{pathname, url, size}, ...]"""
        out, cursor = [], None
        while True:
            q = {"prefix": prefix, "limit": "1000", **({"cursor": cursor} if cursor else {})}
            r = self._call(f"{BLOB_API}/?{urllib.parse.urlencode(q)}", method="GET", what=f"list {prefix}", timeout=120)
            out += r.get("blobs", [])
            cursor = r.get("cursor")
            if not (r.get("hasMore") and cursor):
                return out

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                dest.write_bytes(resp.read())
        except urllib.error.HTTPError as e:
            raise ArchiveError(f"blob download {url}: HTTP {e.code}") from e
        return dest

    def dates_for(self, source_id: str) -> list[str]:
        """Dated folders held for a source, newest first."""
        pre = f"{self.prefix}/{source_id}/"
        dates = {b["pathname"][len(pre):].split("/")[0] for b in self.list_prefix(pre) if "/" in b["pathname"][len(pre):]}
        return sorted((d for d in dates if d), reverse=True)

    def fetch_folder(self, source_id: str, date_str: str, dest_dir: Path) -> list[Path]:
        """Download one dated folder into dest_dir and return the SOURCE files.

        The archive's own bookkeeping is downloaded too but not returned: manifest.json and the
        per-file .meta.json sidecars describe the pull, and handing them to a parser expecting a
        PDF is how "No /Root object" happens.
        """
        pre = f"{self.prefix}/{source_id}/{date_str}/"
        paths = []
        for b in self.list_prefix(pre):
            rel = b["pathname"][len(pre):]
            if not rel:
                continue
            got = self.download(b["url"], dest_dir / rel)
            if rel != "manifest.json" and not rel.endswith(".meta.json"):
                paths.append(got)
        return sorted(paths)

    def archive_dir(self, source_id: str, day_dir: Path) -> dict:
        """Upload every file under <day_dir> (one source, one date); write and upload manifest.json."""
        files = []
        for p in sorted(x for x in day_dir.rglob("*") if x.is_file() and x.name != "manifest.json"):
            rel = p.relative_to(day_dir).as_posix()
            key = f"{self.prefix}/{source_id}/{day_dir.name}/{rel}"
            entry = {"file": rel, "bytes": p.stat().st_size, "sha256": _sha256(p)}
            if entry["bytes"] > self.max_bytes:
                entry["skipped"] = f"over archive.max_file_mb ({self.max_bytes >> 20} MB); recorded by hash only"
            else:
                r = self.put(p, key)
                entry["blob"] = {"pathname": r.get("pathname", key), "url": r.get("url"), "etag": r.get("etag")}
            files.append(entry)
        manifest = {"engine": self.engine, "source_id": source_id, "date": day_dir.name, "prefix": self.prefix, "files": files}
        mp = day_dir / "manifest.json"
        mp.write_text(json.dumps(manifest, indent=1))
        self.put(mp, f"{self.prefix}/{source_id}/{day_dir.name}/manifest.json")
        return {"engine": self.engine, "uploaded": sum(1 for f in files if "blob" in f), "skipped": sum(1 for f in files if "skipped" in f),
                "bytes": sum(f["bytes"] for f in files if "blob" in f), "manifest": f"{self.prefix}/{source_id}/{day_dir.name}/manifest.json"}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_archive(cfg: dict):
    """VercelBlobArchive when BLOB_READ_WRITE_TOKEN is set (and IC_ARCHIVE is not 'off'); else None."""
    if os.environ.get("IC_ARCHIVE", "").lower() in ("off", "0", "none"):
        return None
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        return None
    a = cfg.get("archive") or {}
    return VercelBlobArchive(token, prefix=a.get("prefix", "ic-sources"), access=a.get("access", "private"),
                             max_file_mb=a.get("max_file_mb", 100), store_id=os.environ.get("BLOB_STORE_ID"))


# ---------------------------------------------------------------- CLI
def _verify(a: VercelBlobArchive) -> int:
    """One real round trip against the store: put → head → delete. Proves the token, the store and
    the key layout before a quarterly run depends on them. Leaves nothing behind."""
    import tempfile
    from datetime import date
    key = f"{a.prefix}/_verify/{date.today().isoformat()}/probe.txt"
    body = f"ic-factory-database archive verify {time.time():.0f}\n".encode()
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "probe.txt"; f.write_bytes(body)
        print(f"store {a.store_id or '(unparsed)'} · access {a.access} · prefix {a.prefix}")
        r = a.put(f, key)
        print(f"  PUT    {r.get('pathname', key)}  → {r.get('url', '(no url in response)')}")
        meta = a.head(r.get("url") or key)
        size = meta.get("size")
        if size != len(body):
            print(f"  HEAD   size {size} ≠ {len(body)} written", file=sys.stderr); return 1
        print(f"  HEAD   {meta.get('pathname')}  {size} bytes  ok")
        a.delete([r.get("url") or key])
        print("  DELETE probe removed")
    print("archive verify: ok")
    return 0


def _put(a: VercelBlobArchive, source_id: str, files: list[str], date_str: str | None) -> int:
    """Upload hand-obtained files for one source, keyed the way the run expects to read them.

    The five sources that publish nothing fetchable (docs/manual-uploads.md) enter the pipeline
    this way. Constructing <prefix>/<source_id>/<date>/<file> by hand in a dashboard is the easy
    thing to get wrong — and a file at the wrong key is invisible to the run, which then reports
    the source as EMPTY. This builds the key and the manifest the same way a fetch would.
    """
    import shutil, tempfile
    from datetime import date as _date
    day = date_str or _date.today().isoformat()
    paths = [Path(f) for f in files]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print("no such file: " + ", ".join(str(p) for p in missing), file=sys.stderr); return 1
    with tempfile.TemporaryDirectory() as d:
        day_dir = Path(d) / day
        day_dir.mkdir()
        for p in paths:
            shutil.copy2(p, day_dir / p.name)
        r = a.archive_dir(source_id, day_dir)
    print(f"  {source_id}  {r['uploaded']} file(s), {r['bytes']} bytes → {a.prefix}/{source_id}/{day}/")
    for p in paths:
        print(f"      {p.name}")
    if r["skipped"]:
        print(f"  WARNING {r['skipped']} file(s) over archive.max_file_mb were recorded by hash but NOT "
              f"uploaded — the run cannot read those back", file=sys.stderr)
    print(f"\nconfirm with:  python -m pipeline.sources.refresh --list")
    return 0


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(prog="python -m pipeline.archive")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("verify", help="put a probe file, read it back, delete it — proves the token and store")
    pu = sub.add_parser("put", help="upload hand-obtained files for one source (see docs/manual-uploads.md)")
    pu.add_argument("source_id"); pu.add_argument("file", nargs="+")
    pu.add_argument("--date", help="date folder to write (default: today)")
    ls = sub.add_parser("list", help="print the manifest one pull recorded")
    ls.add_argument("source_id"); ls.add_argument("date")
    args = ap.parse_args(argv)

    cfg = load_yaml(root / "registry" / "config.yaml")
    a = open_archive(cfg)
    if a is None:
        print("no archive: BLOB_READ_WRITE_TOKEN is not set (or IC_ARCHIVE=off)", file=sys.stderr); return 1
    if args.cmd == "verify":
        return _verify(a)
    if args.cmd == "put":
        return _put(a, args.source_id, args.file, args.date)
    key = f"{a.prefix}/{args.source_id}/{args.date}/manifest.json"
    local = root / cfg["storage"]["local_cache"] / args.source_id / args.date / "manifest.json"
    if local.exists():
        print(local.read_text()); return 0
    print(a.head(key)); return 0


if __name__ == "__main__":
    sys.exit(main())
