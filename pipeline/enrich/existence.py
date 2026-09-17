"""Stage 12 — evidence that a plant may no longer be there. Flags only, never deletions.

Nothing here is a judgement about a company. It is three observations that, taken together, are
worth a human's attention, and each is recorded with the evidence that produced it so the human
can disagree. Retiring a facility is a decision, and decisions live in
control/operator_assertions.csv, which outranks every source including this one.

The footprint threshold is measured, not chosen, and it is measured per industry. A small building
is evidence about the *location*, not proof about the plant — which is why it is a flag and not a
deletion — but "small" only means anything relative to what the trade builds. See NAICS_MEDIAN_SQFT
below for the measurement and for why a single number was wrong.
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

STALE_YEARS = 5              # a licence this long expired is not a live liveness signal

# "Implausibly small" is a statement about an industry, not about square feet. Measured over the
# 667 footprints in golden on 2026-09-17, a flat 10,000 sqft threshold was exactly backwards:
#
#                              flat 10,000   20% of own median
#   321214 truss   median 14,145   58 of 153            11
#   321991 homes   median 87,303   18 of 121            22
#
# A 10,000 sqft truss shop is an ordinary truss shop — that is near the median for the trade. A
# 10,000 sqft manufactured-home plant would be remarkable. The flat number flagged 38% of truss
# shops and 15% of home plants, when the second group is the one where small is strange.
#
# So the threshold is a fraction of what the facility's own industry builds. It fires on 13% of
# measured footprints instead of 25%, and redistributes those onto the facilities where small
# actually is evidence. Re-derive with the query in docs/enrichment.md when the measured set grows.
NAICS_MEDIAN_SQFT = {            # median measured footprint, and the n it rests on
    "332311": 49_534,            # prefabricated metal buildings        n=162
    "321214": 14_145,            # truss and engineered wood members    n=153
    "321991": 87_303,            # manufactured homes                   n=121
    "321992": 18_894,            # prefabricated wood buildings         n= 99
    "321213": 25_404,            # engineered wood members              n= 22
    "332312": 50_008,            # fabricated structural metal          n= 13
}
DEFAULT_MEDIAN_SQFT = 33_236     # all 667, for a facility with no NAICS or a trade too thin to fit
SMALL_FRACTION = 0.20


def small_sqft(naics: str | None) -> int:
    """The footprint below which this facility's size is evidence about its location."""
    return round(SMALL_FRACTION * NAICS_MEDIAN_SQFT.get((naics or "")[:6], DEFAULT_MEDIAN_SQFT))


def _expired_years(row: dict, today: date) -> float | None:
    raw = (row.get("expiry_date") or "").strip()
    if not raw:
        return None
    try:
        y, m, d = (int(x) for x in raw[:10].split("-"))
        return (today - date(y, m, d)).days / 365.25
    except (ValueError, TypeError):
        return None


def run(rows: list[dict], out: Path, today: date | None = None) -> dict:
    """rows: the golden snapshot. Reads stage 11's output for footprint evidence when present."""
    from ._db import assertion
    today = today or date.today()
    fp: dict[str, dict] = {}
    f = Path(out) / "footprint.rows.json"
    if f.exists():
        fp = {r["facility_id"]: r for r in json.loads(f.read_text())}

    asserts, reasons = [], {}
    for row in rows:
        fid = row["facility_id"]
        ev = []
        m = fp.get(fid)
        small = small_sqft(row.get("naics"))
        if m and m.get("building_sqft") and m["building_sqft"] < small:
            ev.append(f"footprint {m['building_sqft']:,} sqft is below {small:,}, "
                      f"the bar for NAICS {(row.get('naics') or '?')[:6]}")
        if m and not m.get("building_sqft") and m.get("n_nearby"):
            ev.append(f"no building within 30m of the coordinate ({m['n_nearby']} nearby)")
        yrs = _expired_years(row, today)
        if yrs is not None and yrs > STALE_YEARS:
            ev.append(f"licence expired {yrs:.0f} years ago ({row['expiry_date']})")
        if (row.get("status") or "").strip().lower() in {"expired", "denied", "inactive", "revoked"}:
            ev.append(f"source status is {row['status']!r}")
        # one observation is noise; two independent ones are worth a look
        if len(ev) < 2:
            continue
        for e in ev:
            key = e.split(" ")[0] + " " + e.split(" ")[1]
            reasons[key] = reasons.get(key, 0) + 1
        asserts.append(assertion(fid, "existence_flag", "review",
                                 source_id="enrich:existence", basis="advisory",
                                 evidence="; ".join(ev)))
    return {"examined": len(rows), "flagged": len(asserts), "assertions": asserts,
            "reasons": reasons, "stale_years": STALE_YEARS,
            "small_sqft_by_naics": {k: round(SMALL_FRACTION * v)
                                    for k, v in NAICS_MEDIAN_SQFT.items()},
            "small_sqft_default": round(SMALL_FRACTION * DEFAULT_MEDIAN_SQFT)}
