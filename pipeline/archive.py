"""Raw-source archive: every file Layer 1 fetched, kept off the runner.

Engine today: Vercel Blob, selected when BLOB_READ_WRITE_TOKEN is set. Objects are keyed
<prefix>/<source_id>/<date>/<file> (the layout registry/config.yaml has always described), access
private, overwrite allowed (a re-run on the same day replaces the same keys). A manifest.json per
source/date lists every file with size and sha256, including files too large to upload — the
EPA national_combined.zip (~730 MB) is recorded by hash and skipped; the fetcher archives the
filtered slice it actually used instead (pipeline/sources/epa_frs.py).

The request contract is the one @vercel/blob 2.x `put()` sends (read from the SDK source):
PUT https://vercel.com/api/blob/?pathname=…  with  authorization: Bearer <token>, x-api-version: 12,
x-vercel-blob-access, x-content-type, x-add-random-suffix, x-allow-overwrite, x-vercel-blob-store-id.

No token → no archive, and the run record says so. Token present but an upload fails → the
source fails at Layer 1 (never silently skipped). IC_ARCHIVE=off disables it explicitly.
"""
from __future__ import annotations
import hashlib, json, mimetypes, os, random, time, urllib.parse, urllib.request
from pathlib import Path

BLOB_API = os.environ.get("VERCEL_BLOB_API_URL", "https://vercel.com/api/blob")
BLOB_API_VERSION = "12"


class ArchiveError(Exception):
    pass


class VercelBlobArchive:
    engine = "vercel_blob"

    def __init__(self, token: str, *, prefix: str = "ic-sources", access: str = "private", max_file_mb: int = 100):
        self.token, self.prefix, self.access, self.max_bytes = token, prefix.strip("/"), access, int(max_file_mb * 1024 * 1024)
        parts = token.split("_")
        self.store_id = parts[3] if len(parts) > 3 else ""

    def put(self, path: Path, pathname: str) -> dict:
        body = path.read_bytes()
        headers = {
            "authorization": f"Bearer {self.token}", "x-api-version": BLOB_API_VERSION,
            "x-vercel-blob-access": self.access, "x-add-random-suffix": "0", "x-allow-overwrite": "1",
            "x-content-type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "x-content-length": str(len(body)), "x-vercel-blob-store-id": self.store_id,
            "x-api-blob-request-id": f"{self.store_id}:{int(time.time()*1000)}:{random.random().hex()[2:]}",
            "x-api-blob-request-attempt": "0",
        }
        url = f"{BLOB_API}/?{urllib.parse.urlencode({'pathname': pathname})}"
        req = urllib.request.Request(url, data=body, method="PUT", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise ArchiveError(f"blob put {pathname}: HTTP {e.code} {e.read()[:300]!r}") from e
        except urllib.error.URLError as e:
            raise ArchiveError(f"blob put {pathname}: {e.reason}") from e

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
    return VercelBlobArchive(token, prefix=a.get("prefix", "ic-sources"), access=a.get("access", "private"), max_file_mb=a.get("max_file_mb", 100))
