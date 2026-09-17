"""Stage 12 — evidence that a plant may no longer be there. Flags only, never deletions.

Nothing here is a judgement about a company. It is three observations that, taken together, are
worth a human's attention, and each is recorded with the evidence that produced it so the human
can disagree. Retiring a facility is a decision, and decisions live in
control/operator_assertions.csv, which outranks every source including this one.

The footprint threshold is measured, not chosen. Across the 30 facilities verified against
satellite imagery by hand, every geocode that landed on the wrong parcel produced a footprint under
9,000 sqft (a house, a shed, a unit beside a driveway), while correctly located plants had a median
of 78,000. Both rows the human marked "uncertain" were flagged by footprint alone, before anyone
looked at the image. A small building is therefore evidence about the *location*, not proof about
the plant — which is why it is a flag and not a deletion.
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

SMALL_SQFT = 10_000          # below this, a claimed plant is more likely a mislocation
STALE_YEARS = 5              # a licence this long expired is not a live liveness signal


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
        if m and m.get("building_sqft") and m["building_sqft"] < SMALL_SQFT:
            ev.append(f"footprint {m['building_sqft']:,} sqft is below {SMALL_SQFT:,}")
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
            "reasons": reasons, "small_sqft_threshold": SMALL_SQFT, "stale_years": STALE_YEARS}
