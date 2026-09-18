import csv
from pipeline.run import _write_csv


def test_csv_header_is_the_union_of_keys_not_the_first_rows(tmp_path):
    """Run 35276704513: the first golden row had no sq_ft, so nobody's sq_ft was printed."""
    rows = [{"facility_id": "IC-1", "name": "A"}, {"facility_id": "IC-2", "name": "B", "sq_ft": "75000", "website": "x"}]
    p = tmp_path / "g.csv"; _write_csv(p, rows)
    got = list(csv.DictReader(open(p)))
    assert list(got[0].keys()) == ["facility_id", "name", "sq_ft", "website"]
    assert got[1]["sq_ft"] == "75000" and got[0]["sq_ft"] == ""
