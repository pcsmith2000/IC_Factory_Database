"""Enrichment stages. Network-free: the area maths is pinned against hand-computed truth."""
import math
import pytest
from pipeline.enrich.footprint import wkt_area_m2, file_for, M2_FT2


def _box(lat, lon, d):
    return f"POLYGON(({lon} {lat},{lon+d} {lat},{lon+d} {lat+d},{lon} {lat+d},{lon} {lat}))"


@pytest.mark.parametrize("lat", [0.0, 34.6, 45.0, 60.0])
def test_area_tracks_latitude(lat):
    """DuckDB's ST_Area_Spheroid returns one area for this polygon at every latitude — right at the
    equator, 21% high at 34N, 99% high at 60N. A national dataset would inflate silently, so the
    area must actually shrink as the meridians converge."""
    d = 0.001
    got = wkt_area_m2(_box(lat, -82.6, d))
    truth = (d * 111320) * (d * 111320 * math.cos(math.radians(lat + d / 2)))
    assert abs(got - truth) / truth < 0.005, f"at {lat}N: {got:,.0f} vs {truth:,.0f} m2"


def test_area_at_60N_is_half_that_at_the_equator():
    a0 = wkt_area_m2(_box(0.0, 0.0, 0.001))
    a60 = wkt_area_m2(_box(60.0, 0.0, 0.001))
    assert 0.48 < a60 / a0 < 0.52


def test_holes_are_subtracted():
    outer = "0 0,0.001 0,0.001 0.001,0 0.001,0 0"
    inner = "0.0002 0.0002,0.0002 0.0008,0.0008 0.0008,0.0008 0.0002,0.0002 0.0002"
    solid = wkt_area_m2(f"POLYGON(({outer}))")
    holed = wkt_area_m2(f"POLYGON(({outer}),({inner}))")
    assert holed < solid and abs(holed - solid * 0.64) / solid < 0.02


def test_multipolygon_sums_its_parts():
    one = wkt_area_m2(_box(34.6, -82.6, 0.001))
    two = wkt_area_m2(f"MULTIPOLYGON(({_box(34.6,-82.6,0.001)[8:-1]}),({_box(34.6,-82.5,0.001)[8:-1]}))")
    assert abs(two - 2 * one) / one < 0.01


def test_file_index_lookup_picks_the_covering_file():
    idx = [{"file": "a", "xmin": -180, "xmax": -175, "ymin": -78, "ymax": -21},
           {"file": "b", "xmin": -84.6, "xmax": -81.9, "ymin": 30.0, "ymax": 35.8}]
    assert file_for(idx, 34.611077, -82.604892) == "b"
    assert file_for(idx, 47.6, -122.3) is None     # not covered -> caller records a reason


def test_square_feet_conversion():
    assert abs(wkt_area_m2(_box(34.6, -82.6, 0.001)) * M2_FT2 - 109_500) < 3_000
