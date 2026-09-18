"""The blob copy is a move between Vercel teams, and the store is not a cache: archive.mode is
blob-only, so a half-copied store fails a run at Layer 1 rather than degrading."""
from pathlib import Path
import pipeline.archive as archive


class FakeStore:
    """Enough of VercelBlobArchive to drive _copy: a dict of pathname -> bytes."""
    def __init__(self, store_id, objs=None):
        self.store_id, self.prefix, self.access, self.max_bytes = store_id, "ic-sources", "public", 1 << 30
        self.objs = dict(objs or {})
        self.puts = []

    def list_prefix(self, prefix):
        return [{"pathname": k, "url": f"https://blob/{k}", "size": len(v)}
                for k, v in self.objs.items() if k.startswith(prefix)]

    def download(self, url, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objs[url.replace("https://blob/", "")]); return dest

    def put(self, path: Path, pathname: str, **kw):
        self.puts.append(pathname); self.objs[pathname] = path.read_bytes(); return {"pathname": pathname}


def _run(monkeypatch, src, dst, dry_run=False, prefixes=("ic-sources/",)):
    monkeypatch.setattr(archive, "VercelBlobArchive", lambda token, **kw: dst)
    return archive._copy(src, "dest-token", list(prefixes), dry_run)


def test_it_copies_what_is_missing_and_skips_what_already_matches(monkeypatch, capsys):
    src = FakeStore("AAA", {"ic-sources/a/1.html": b"one", "ic-sources/b/2.html": b"twotwo"})
    dst = FakeStore("BBB", {"ic-sources/a/1.html": b"one"})          # already there, same size
    assert _run(monkeypatch, src, dst) == 0
    assert dst.puts == ["ic-sources/b/2.html"]                        # only the missing one moved
    assert "1 copied, 1 already there, 0 failed" in capsys.readouterr().out


def test_a_destination_object_of_a_DIFFERENT_size_is_re_copied(monkeypatch):
    """A truncated object is worse than a missing one: it reads as present and parses as garbage."""
    src = FakeStore("AAA", {"ic-sources/a/1.html": b"the whole file"})
    dst = FakeStore("BBB", {"ic-sources/a/1.html": b"trunc"})
    assert _run(monkeypatch, src, dst) == 0
    assert dst.objs["ic-sources/a/1.html"] == b"the whole file"


def test_a_dry_run_transfers_nothing(monkeypatch):
    src = FakeStore("AAA", {"ic-sources/a/1.html": b"one"})
    dst = FakeStore("BBB")
    assert _run(monkeypatch, src, dst, dry_run=True) == 0
    assert dst.puts == []


def test_copying_a_store_onto_itself_is_refused(monkeypatch, capsys):
    src = FakeStore("SAME", {"ic-sources/a/1.html": b"one"})
    assert _run(monkeypatch, src, FakeStore("SAME")) == 1
    assert "SAME store" in capsys.readouterr().err


def test_a_short_destination_fails_the_command_even_when_every_put_returned(monkeypatch, capsys):
    """The verification re-lists the destination rather than trusting the loop's own count."""
    src = FakeStore("AAA", {"ic-sources/a/1.html": b"one"})
    dst = FakeStore("BBB")
    dst.put = lambda path, pathname, **kw: dst.puts.append(pathname)   # accepts, stores nothing
    assert _run(monkeypatch, src, dst) == 1
    assert "SHORT" in capsys.readouterr().out
