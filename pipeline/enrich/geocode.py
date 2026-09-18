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
# 2,500 lookups a day are free on a pay-as-you-go account and do not roll over; past that it bills
# at $1 per 1,000. The account is no longer free-tier-only, and that removed a guard rather than a
# limit: the 403 that used to stop a run at 2,500 now never comes, and the overage is silent. So
# this number is a budget line, not a wall — see --geocode-limit, which is the dial that matters.
FREE_TIER_PER_DAY = 2500
COST_PER_1000_USD = 1.00


class GeocodioError(RuntimeError):
    pass


class GeocodioQuotaExhausted(GeocodioError):
    """The day's free-tier lookups are spent. Not a failure of this stage — a ceiling on it.

    It reads like the footprint file ceiling: the addresses that did not get looked up are deferred,
    not dropped. Nothing about them changed, the stage selects them again next run, and the
    assertions it did earn before the ceiling are still written. Crashing here would throw away
    those, and would make a red run out of a stage that behaved correctly.
    """


def one_line(r: dict) -> str:
    tail = " ".join(p for p in [r.get("city") or "", r.get("state") or "", r.get("zip") or ""] if p)
    return f"{(r.get('address') or '').strip()}, {tail}".strip().rstrip(",")


def _post(queries: list[str], key: str) -> tuple[list[dict], str]:
    """Returns (results in input order, reason the run stopped early or "").

    Stopping early keeps whatever earlier batches returned. A 403 on the third batch does not make
    the first two worthless, and results come back in input order, so the caller pairs what it got
    with the head of its own list and defers the tail.
    """
    out: list[dict] = []
    for i in range(0, len(queries), BATCH):
        req = urllib.request.Request(f"{API}?api_key={key}",
                                     data=json.dumps(queries[i:i + BATCH]).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                out.extend(json.loads(resp.read()).get("results", []))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (402, 403) and "free tier" in body.lower():
                return out, body
            raise GeocodioError(f"Geocodio returned {e.code}: {body}") from None
    return out, ""


def run(rows: list[dict], key: str | None = None, db=None) -> dict:
    """rows: facilities with an address and no coordinate. Returns assertions plus a report.

    `db` enables the lookup ledger. A geocode is a pure function of an address string, so an
    address already looked up — for this facility, for another one sharing it, or under a facility
    id that has since changed — is answered from cache_lookup and never billed twice.
    """
    from ._db import assertion
    from . import cache
    key = key or os.environ.get("GEOCODIO_API_KEY")
    if not key:
        raise GeocodioError("GEOCODIO_API_KEY is not set (free key: https://dash.geocod.io/apikey)")
    todo = [r for r in rows if (r.get("address") or "").strip()]
    if not todo:
        return _report([], [], {}, [], [], [], "")
    if len(todo) > FREE_TIER_PER_DAY:
        print(f"  note: {len(todo)} lookups exceeds the {FREE_TIER_PER_DAY}/day free allowance; "
              f"the excess bills at ${COST_PER_1000_USD:.2f}/1000", file=sys.stderr)

    # Split the work before spending anything: what the ledger already knows, and what it does not.
    lines = {r["facility_id"]: one_line(r) for r in todo}
    cached: dict[str, dict] = {}
    if db is not None:
        cache.ensure(db)
        for r in todo:
            hit = cache.get(db, cache.geocode_key(lines[r["facility_id"]]), provider="geocodio")
            if hit:
                cached[r["facility_id"]] = hit["result"]
    fresh = [r for r in todo if r["facility_id"] not in cached]

    results, stopped = _post([lines[r["facility_id"]] for r in fresh], key) if fresh else ([], "")
    done, deferred = fresh[:len(results)], fresh[len(results):]

    # A cached answer re-enters the same pipeline as a fresh one, so there is one code path deciding
    # what becomes a coordinate and what becomes a quality flag.
    for r in todo:
        if r["facility_id"] in cached:
            done.append(r)
            results.append(cached[r["facility_id"]])
    asserts, flags, mix = [], [], {}
    for row, res in zip(done, results):
        hits = (res.get("response") or {}).get("results") or []
        at = hits[0].get("accuracy_type", "unknown") if hits else "no_result"
        mix[at] = mix.get(at, 0) + 1
        if db is not None and row["facility_id"] not in cached:
            # Both outcomes are worth recording. An address Geocodio cannot place costs exactly as
            # much to re-ask as one it can, and the tail that never matches is most of the waste.
            cache.put(db, cache.geocode_key(lines[row["facility_id"]]), "geocode",
                      lines[row["facility_id"]], res, bool(hits), "geocodio")
        flags.append({"facility_id": row["facility_id"], "accuracy_type": at,
                      "accuracy": hits[0].get("accuracy") if hits else None,
                      "dataset": hits[0].get("source") if hits else None})
        if at not in STORABLE:
            # Recorded so the next run does not spend a lookup re-learning it. Geocodio returns the
            # same answer for the same address until its underlying parcel data changes, and these
            # addresses are the tail that never matches: without this, every run burns its ceiling
            # on the rows it already knows it cannot place, and never reaches new ones.
            asserts.append(assertion(
                row["facility_id"], "geocode_quality", at,
                source_id="geocode:geocodio", basis="not_rooftop",
                confidence=hits[0].get("accuracy") if hits else None,
                evidence=f"geocodio:{(hits[0].get('source') if hits else 'no_result')}"))
            continue                                   # a flag only; never a coordinate
        loc = hits[0]["location"]
        asserts.append(assertion(
            row["facility_id"], "lat_lon", f"{loc['lat']},{loc['lng']}",
            source_id="geocode:geocodio", basis="rooftop",
            confidence=hits[0].get("accuracy"),
            # the underlying dataset is part of the licence question, not just the accuracy one:
            # Geocodio may store and sell results only as its own data sources permit
            evidence=f"geocodio:{hits[0].get('source', '?')}"))
    # count coordinates, not assertions: the quality flags are assertions too, and reporting them
    # as "rooftop stored" would say a run placed facilities it explicitly declined to place
    coords = [a for a in asserts if a["field"] == "lat_lon"]
    return _report(done, asserts, mix, flags, todo, deferred, stopped, cached)


def _report(done, asserts, mix, flags, todo, deferred, stopped, cached=()) -> dict:
    """Every exit from run() returns this shape.

    The nothing-to-do exit used to return four keys of its own, and the reporter in run.py reads
    eleven. A run with no address to place — which is what a drained backlog looks like — crashed
    on KeyError: 'stored' after stage 9 had already spent half an hour. An empty run is a normal
    outcome, not an error, and the only way to keep the two shapes honest is to build both here.
    """
    coords = [a for a in asserts if a["field"] == "lat_lon"]
    return {"requested": len(done), "assertions": asserts, "accuracy_type": mix, "flags": flags,
            "stored": len(coords), "quality_flags_recorded": len(asserts) - len(coords),
            "rooftop_pct": round(100 * mix.get("rooftop", 0) / max(1, len(done)), 1),
            "selected": len(todo), "deferred": len(deferred),
            "quota_exhausted": bool(stopped), "quota_message": stopped,
            # What this run would cost if the day's free allowance were already spent. It is an
            # upper bound per run, not a bill: the allowance is daily and shared across every run,
            # so only the sum across a day says what was actually charged.
            "from_cache": len(cached), "billed_lookups": len(done) - len(cached),
            "billable_if_allowance_spent_usd":
                round((len(done) - len(cached)) * COST_PER_1000_USD / 1000, 3)}
