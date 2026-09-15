import io, json, urllib.request
from pathlib import Path
import pytest
from pipeline import archive


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass


def test_vercel_blob_put_matches_the_sdk_contract_and_manifest_skips_big_files(tmp_path: Path, monkeypatch):
    calls = []
    def fake_urlopen(req, timeout=0):
        calls.append(req)
        pn = dict(x.split("=") for x in req.full_url.split("?", 1)[1].split("&"))["pathname"]
        return _Resp(json.dumps({"url": f"https://x.private.blob.vercel-storage.com/{pn}", "pathname": urllib.parse.unquote(pn), "etag": "e"}).encode())
    import urllib.parse
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    day = tmp_path / "tx_tdlr" / "2026-09-15"; day.mkdir(parents=True)
    (day / "2-Certified_Manufacturers_List.pdf").write_bytes(b"%PDF small")
    (day / "2-Certified_Manufacturers_List.pdf.meta.json").write_text("{}")
    (day / "huge.zip").write_bytes(b"0" * (2 * 1024 * 1024))
    a = archive.VercelBlobArchive("vercel_blob_rw_STORE123_secret", prefix="ic-sources", access="private", max_file_mb=1)
    r = a.archive_dir("tx_tdlr", day)
    assert r == {"engine": "vercel_blob", "uploaded": 2, "skipped": 1, "bytes": 12, "manifest": "ic-sources/tx_tdlr/2026-09-15/manifest.json"}
    m = json.loads((day / "manifest.json").read_text())
    huge = next(f for f in m["files"] if f["file"] == "huge.zip")
    assert "skipped" in huge and len(huge["sha256"]) == 64 and "blob" not in huge
    pdf = next(f for f in m["files"] if f["file"].endswith(".pdf"))
    assert pdf["blob"]["pathname"] == "ic-sources/tx_tdlr/2026-09-15/2-Certified_Manufacturers_List.pdf"
    req = calls[0]
    assert req.get_method() == "PUT" and req.full_url.startswith("https://vercel.com/api/blob/?pathname=ic-sources%2Ftx_tdlr%2F2026-09-15%2F")
    h = {k.lower(): v for k, v in req.header_items()}
    assert h["authorization"] == "Bearer vercel_blob_rw_STORE123_secret" and h["x-api-version"] == "12"
    assert h["x-vercel-blob-access"] == "private" and h["x-allow-overwrite"] == "1" and h["x-add-random-suffix"] == "0"
    assert h["x-vercel-blob-store-id"] == "STORE123" and h["x-content-type"] == "application/pdf"
    assert calls[-1].full_url.endswith("manifest.json")   # the manifest itself is archived last


def test_no_token_means_no_archive_and_off_switch(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    assert archive.open_archive({"archive": {}}) is None
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_S_x")
    assert archive.open_archive({"archive": {"max_file_mb": 5}}).max_bytes == 5 * 1024 * 1024
    monkeypatch.setenv("IC_ARCHIVE", "off")
    assert archive.open_archive({}) is None


def test_http_error_is_loud(tmp_path: Path, monkeypatch):
    import urllib.error
    def boom(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, io.BytesIO(b"bad token"))
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    f = tmp_path / "a.txt"; f.write_text("x")
    with pytest.raises(archive.ArchiveError, match="HTTP 403"):
        archive.VercelBlobArchive("vercel_blob_rw_S_x").put(f, "ic-sources/a.txt")
