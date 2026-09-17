"""Stage 10 — address to coordinate, via Geocodio.

Only a rooftop geocode becomes a coordinate. That is a measurement, not a preference: 30 of our
own geocodes were checked against satellite imagery by hand (control/VERIFY-30.csv) and the
accuracy types failed in different ways, not merely at different rates.

    rooftop                80% verified   failures are the right site, point off by 25-110m
    nearest_rooftop_match  30% verified   failures are a different parcel: a vacant lot, a
                                          single-family house, a rural road between pine woods

A point on the wrong parcel is worse than no point at all, because stage 11 will measure the
building under it and the map will render it as though the location were known. So the other
accuracy types are recorded as a geocode-quality flag and nothing more.

Coverage, measured on 200 real addresses: 81.6% rooftop. The types partition perfectly by
provenance — every rooftop came from a local parcel or address-point file, every non-rooftop from
TIGER/Line. There is therefore no free fallback for the tail: it already is the Census geocoder.
"""
from __future__ import annotations
import json, os, sys, urllib.error, urllib.request

API = "https://api.geocod.io/v2/geocode"
BATCH = 1000                       # the endpoint allows 10,000; smaller chunks fail cheaply
STORABLE = {"rooftop"}             # everything else is a flag, never a coordinate
FREE_TIER_PER_DAY = 2500           # shared across everyone using this key


class GeocodioError(RuntimeError):
    pass


def one_line(r: dict) -> str:
    tail = " ".join(p for p in [r.get("city") or "", r.get("state") or "", r.get("zip") or ""] if p)
    return f"{(r.get('address') or '').strip()}, {tail}".strip().rstrip(",")


def _post(queries: list[str], key: str) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(queries), BATCH):
        req = urllib.request.Request(f"{API}?api_key={key}",
                                     data=json.dumps(queries[i:i + BATCH]).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                out.extend(json.loads(resp.read()).get("results", []))
        except urllib.error.HTTPError as e:
            raise GeocodioError(f"Geocodio returned {e.code}: "
                                f"{e.read().decode('utf-8', 'replace')[:300]}") from None
    return out


def run(rows: list[dict], key: str | None = None) -> dict:
    """rows: facilities with an address and no coordinate. Returns assertions plus a report."""
    from ._db import assertion
    key = key or os.environ.get("GEOCODIO_API_KEY")
    if not key:
        raise GeocodioError("GEOCODIO_API_KEY is not set (free key: https://dash.geocod.io/apikey)")
    todo = [r for r in rows if (r.get("address") or "").strip()]
    if not todo:
        return {"requested": 0, "assertions": [], "accuracy_type": {}, "flags": []}
    if len(todo) > FREE_TIER_PER_DAY:
        print(f"  note: {len(todo)} lookups exceeds the {FREE_TIER_PER_DAY}/day free tier; "
              f"use --sample or expect to be billed", file=sys.stderr)

    results = _post([one_line(r) for r in todo], key)
    asserts, flags, mix = [], [], {}
    for row, res in zip(todo, results):
        hits = (res.get("response") or {}).get("results") or []
        at = hits[0].get("accuracy_type", "unknown") if hits else "no_result"
        mix[at] = mix.get(at, 0) + 1
        flags.append({"facility_id": row["facility_id"], "accuracy_type": at,
                      "accuracy": hits[0].get("accuracy") if hits else None,
                      "dataset": hits[0].get("source") if hits else None})
        if at not in STORABLE:
            continue                                   # a flag only; never a coordinate
        loc = hits[0]["location"]
        asserts.append(assertion(
            row["facility_id"], "lat_lon", f"{loc['lat']},{loc['lng']}",
            source_id="geocode:geocodio", basis="rooftop",
            confidence=hits[0].get("accuracy"),
            # the underlying dataset is part of the licence question, not just the accuracy one:
            # Geocodio may store and sell results only as its own data sources permit
            evidence=f"geocodio:{hits[0].get('source', '?')}"))
    return {"requested": len(todo), "assertions": asserts, "accuracy_type": mix, "flags": flags,
            "stored": len(asserts),
            "rooftop_pct": round(100 * mix.get("rooftop", 0) / max(1, len(todo)), 1)}
