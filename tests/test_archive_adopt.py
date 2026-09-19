"""`adopt` exists because the store is an input and its folders are chosen by hand.

A CSV uploaded through the Vercel dashboard lands wherever the person making it put it —
`ic-sources/additional_directories_2026-09-18/` for one collection. acquire.from_blob() resolves a
source to `arch.dates_for(sid)` and then `fetch_folder(sid, date)` and can see nothing else, so
that file is invisible to the run however correct its contents. adopt copies it into the layout
Layer 1 reads, without deleting the arrival.
"""
import pipeline.archive as archive
from tests.test_archive_copy import FakeStore


def test_it_copies_the_upload_into_the_source_date_folder():
    s = FakeStore("AAA", {"ic-sources/additional_directories_2026-09-18/evidence.csv": b"a,b\n1,2\n"})
    assert archive._adopt(s, "ic-sources/additional_directories_2026-09-18/", "ic_directories_more",
                          "2026-09-18", dry_run=False) == 0
    assert s.puts == ["ic-sources/ic_directories_more/2026-09-18/evidence.csv"]


def test_the_arrival_is_kept_where_it_was_put():
    """The object the human actually created stays; the pipeline gets a copy, not custody."""
    s = FakeStore("AAA", {"ic-sources/drop/x.csv": b"one"})
    archive._adopt(s, "ic-sources/drop/", "sid", "2026-09-18", dry_run=False)
    assert "ic-sources/drop/x.csv" in s.objs


def test_re_adopting_the_same_file_is_a_no_op():
    s = FakeStore("AAA", {"ic-sources/drop/x.csv": b"one",
                          "ic-sources/sid/2026-09-18/x.csv": b"one"})
    assert archive._adopt(s, "ic-sources/drop/", "sid", "2026-09-18", dry_run=False) == 0
    assert s.puts == []


def test_a_destination_object_of_a_different_size_is_replaced():
    """Same name, fewer bytes: a truncated upload reads as present and parses as garbage."""
    s = FakeStore("AAA", {"ic-sources/drop/x.csv": b"the whole file",
                          "ic-sources/sid/2026-09-18/x.csv": b"trunc"})
    archive._adopt(s, "ic-sources/drop/", "sid", "2026-09-18", dry_run=False)
    assert s.objs["ic-sources/sid/2026-09-18/x.csv"] == b"the whole file"


def test_a_dry_run_transfers_nothing():
    s = FakeStore("AAA", {"ic-sources/drop/x.csv": b"one"})
    assert archive._adopt(s, "ic-sources/drop/", "sid", "2026-09-18", dry_run=True) == 0
    assert s.puts == []


def test_adopting_nothing_is_an_error_not_a_quiet_success(capsys):
    """Silence here would mean dispatching a run against a source with no input at all."""
    assert archive._adopt(FakeStore("AAA"), "ic-sources/typo/", "sid", "2026-09-18", False) == 1
    assert "nothing under" in capsys.readouterr().err


def test_ls_prints_every_object_and_the_total():
    s = FakeStore("AAA", {"ic-sources/a/1.csv": b"one", "ic-sources/b/2.csv": b"twotwo"})
    assert archive._ls(s, "ic-sources/") == 0
