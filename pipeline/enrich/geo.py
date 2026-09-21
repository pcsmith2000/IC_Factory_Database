"""Is a coordinate where the facility says it is?

The warehouse carries enrichment assertions across releases by facility id. When two releases
were built from diverging id registries (a branch release and a main release each issuing
IC-numbers in their own order), the same number came to name different plants, and a rooftop
geocoded for one plant was carried onto another. The symptom is unmistakable in the data and
needs no external service to detect: the golden coordinate lies outside the golden state.
Measured on 2026-09-21, 1,157 of 4,857 located facilities failed this check.

The boxes are generous (about 0.3 degrees beyond each state's extent), so a plant on a state line
passes and only a point in a different part of the country fails. This is a coarse gate, not a
rooftop check: passing it says the coordinate is plausible, not that it is right.
"""
from __future__ import annotations

# (lat_min, lat_max, lon_min, lon_max), padded by ~0.3 degrees.
STATE_BOXES = {
    'AL': (30.1, 35.3, -88.8, -84.6), 'AK': (51.0, 72.0, -180.0, -129.0), 'AZ': (31.0, 37.3, -115.1, -108.7),
    'AR': (32.7, 36.8, -94.9, -89.3), 'CA': (32.2, 42.3, -124.8, -113.8), 'CO': (36.7, 41.3, -109.4, -101.7),
    'CT': (40.7, 42.3, -74.0, -71.5), 'DE': (38.2, 40.1, -75.9, -74.7), 'FL': (24.2, 31.3, -87.9, -79.7),
    'GA': (30.0, 35.3, -85.9, -80.5), 'HI': (18.5, 22.5, -160.5, -154.5), 'ID': (41.7, 49.3, -117.6, -110.7),
    'IL': (36.7, 42.8, -91.8, -87.2), 'IN': (37.5, 42.0, -88.4, -84.5), 'IA': (40.1, 43.8, -96.9, -89.8),
    'KS': (36.7, 40.3, -102.4, -94.3), 'KY': (36.2, 39.4, -89.9, -81.6), 'LA': (28.6, 33.3, -94.4, -88.5),
    'ME': (42.7, 47.8, -71.4, -66.6), 'MD': (37.6, 39.9, -79.8, -74.7), 'MA': (41.1, 43.0, -73.8, -69.6),
    'MI': (41.4, 48.6, -90.7, -82.1), 'MN': (43.2, 49.7, -97.6, -89.2), 'MS': (29.9, 35.3, -91.9, -87.8),
    'MO': (35.7, 40.9, -95.9, -88.8), 'MT': (44.0, 49.3, -116.4, -103.7), 'NE': (39.7, 43.3, -104.4, -95.0),
    'NV': (34.7, 42.3, -120.3, -113.7), 'NH': (42.4, 45.6, -72.9, -70.3), 'NJ': (38.6, 41.7, -75.9, -73.5),
    'NM': (31.0, 37.3, -109.4, -102.7), 'NY': (40.2, 45.3, -80.1, -71.5), 'NC': (33.5, 36.9, -84.6, -75.1),
    'ND': (45.6, 49.3, -104.4, -96.2), 'OH': (38.1, 42.3, -85.1, -80.2), 'OK': (33.3, 37.3, -103.3, -94.1),
    'OR': (41.7, 46.6, -124.9, -116.1), 'PA': (39.4, 42.6, -80.8, -74.4), 'RI': (41.0, 42.3, -72.2, -70.8),
    'SC': (31.7, 35.5, -83.7, -78.2), 'SD': (42.2, 46.2, -104.4, -96.1), 'TN': (34.7, 36.9, -90.6, -81.3),
    'TX': (25.5, 36.8, -107.0, -93.2), 'UT': (36.7, 42.3, -114.4, -108.7), 'VT': (42.4, 45.3, -73.7, -71.2),
    'VA': (36.2, 39.8, -83.9, -75.0), 'WA': (45.2, 49.3, -125.0, -116.6), 'WV': (37.0, 40.9, -82.9, -77.4),
    'WI': (42.2, 47.4, -93.2, -86.4), 'WY': (40.7, 45.3, -111.4, -103.7), 'DC': (38.7, 39.1, -77.2, -76.8),
}


def parse_lat_lon(value) -> tuple[float, float] | None:
    try:
        lat, lon = (float(x) for x in str(value or '').split(',')[:2])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def inside_state(lat: float, lon: float, state: str | None) -> bool | None:
    """True/False when the state is a known US code; None when the check cannot be made."""
    box = STATE_BOXES.get((state or '').strip().upper())
    if not box:
        return None
    a, b, c, d = box
    return a <= lat <= b and c <= lon <= d


def out_of_state(rows: list[dict]) -> list[dict]:
    """Golden rows whose coordinate lies outside their own state. Rows with no state, an unknown
    state code or an unparseable coordinate are not judged."""
    bad = []
    for r in rows:
        point = parse_lat_lon(r.get('lat_lon'))
        if point is None:
            continue
        ok = inside_state(point[0], point[1], r.get('state'))
        if ok is False:
            bad.append({'facility_id': r.get('facility_id') or r.get('facility_key'), 'lat_lon': r['lat_lon'],
                        'state': r.get('state'), 'source': r.get('lat_lon__source')})
    return bad
