"""A source's bytes are wherever the person who made them put them.

The Blob store is an INPUT — `archive.mode: blob-only` means Layer 1 reads it and never scrapes —
but files arrive in it by hand, through the Vercel dashboard, into a folder named after the
COLLECTION rather than after any source id. acquire.from_blob() otherwise resolves a source only
through dates_for(sid) + fetch_folder(sid, date), so such a file is unreachable however correct it
is. `blob_path` in the registry says where to look, and keeps the registry the one place that
answers "where does this source's data come from".
"""
from pathlib import Path
import pytest
import pipeline.acquire as acquire


class FakeArchive:
    prefix = "ic-sources"

    def __init__(self, files=("evidence.csv",)):
        self.files, self.asked_prefix, self.dates_asked = files, None, []

    def fetch_prefix(self, prefix, dest_dir: Path):
        self.asked_prefix = prefix
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = []
        for n in self.files:
            (dest_dir / n).write_text("company_name\nx\n")
            out.append(dest_dir / n)
        return out

    def dates_for(self, sid):
        self.dates_asked.append(sid)
        return []

    def fetch_folder(self, sid, date_str, dest):        # pragma: no cover - must not be reached
        raise AssertionError("blob_path was set; the dated layout must not be consulted")


class FakeModule:
    def __init__(self):
        self.saw = None

    def parse(self, files, source):
        self.saw = list(files)
        return [{"name_verbatim": "x"}]


def _pull(monkeypatch, tmp_path, source, arch, mod):
    monkeypatch.setattr(acquire.importlib, "import_module", lambda name: mod)
    monkeypatch.setattr(acquire._archive, "open_archive", lambda cfg: arch)
    return acquire.pull_source(source, {}, tmp_path / "out", tmp_path / "arch")


def test_the_registry_folder_is_read_instead_of_the_dated_layout(monkeypatch, tmp_path):
    arch, mod = FakeArchive(), FakeModule()
    src = {"id": "ic_directories_more", "acquire": "blob-only",
           "blob_path": "ic-sources/additional_directories_2026-09-18/"}
    _pull(monkeypatch, tmp_path, src, arch, mod)
    assert arch.asked_prefix == "ic-sources/additional_directories_2026-09-18/"
    assert arch.dates_asked == []                     # never even looked for a date folder
    assert [p.name for p in mod.saw] == ["evidence.csv"]


def test_a_source_without_one_still_reads_its_dated_folder(monkeypatch, tmp_path):
    """The convention is not replaced — 27 other blob-only sources still resolve through it."""
    arch, mod = FakeArchive(), FakeModule()
    with pytest.raises(acquire.SourceFailed):          # dates_for returns nothing, as before
        _pull(monkeypatch, tmp_path, {"id": "sbca_cm", "acquire": "blob-only"}, arch, mod)
    assert arch.dates_asked == ["sbca_cm"]


def test_a_blob_path_that_holds_nothing_names_the_folder_it_looked_in(monkeypatch, tmp_path):
    """A typo in the folder name is the whole failure mode here, so the error has to say which
    name was tried — otherwise it reads as 'the upload never happened'."""
    arch, mod = FakeArchive(files=()), FakeModule()
    src = {"id": "x", "acquire": "blob-only", "blob_path": "ic-sources/typo/"}
    with pytest.raises(acquire.SourceFailed, match="ic-sources/typo/"):
        _pull(monkeypatch, tmp_path, src, arch, mod)
