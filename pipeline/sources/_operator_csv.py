"""Shared reader for the state-registry CSVs uploaded by hand into the blob.

Four state registries — CA HCD, NC OSFM, NY DOS, MA BBRS — are form-gated or publish no list at
all, and each was transcribed to one CSV in a single common schema:

    source_id,name,legal_entity,address,city,state,zip,status,expires,registration_id,
    product_type,evidence,source_url,retrieved_date

`evidence` is the transcriber's own note on what the row is and what it is NOT, and it is carried
through to `notes` verbatim rather than summarised, because it is where the caveats live: CA HCD's
addresses are licence addresses, not plants, and NC's third-party file lists certification agencies
rather than manufacturers.
"""
from __future__ import annotations
import csv
from pathlib import Path
from ._common import US_STATES, contract_row, iso_date, pick, require

SCHEMA = {"source_id", "name", "address", "city", "state", "zip", "status", "evidence", "source_url"}


def read(paths: list[Path], source: dict, *, drop_if_evidence: str = "", note: str = "") -> list[dict]:
    """Every row of every CSV in the folder, in the common schema.

    `drop_if_evidence` removes rows whose evidence names them as something other than a plant —
    NC ships its approved third-party inspection agencies in the same shape as its manufacturers,
    and publishing PFS TECO as a modular plant would be a straightforward falsehood.
    """
    files = pick(paths, ".csv")
    require(bool(files), paths[0] if paths else Path(source["id"]),
            "no CSV in the archived folder — upload the transcribed file there, then re-run")
    out, dropped, skipped = [], 0, 0
    for path in sorted(files):
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            rd = csv.DictReader(fh)
            require(bool(rd.fieldnames) and SCHEMA <= set(rd.fieldnames), path,
                    f"columns are {rd.fieldnames}, which is not the transcription schema")
            for r in rd:
                name = (r.get("name") or "").strip()
                if not name:
                    continue
                ev = (r.get("evidence") or "").strip()
                if drop_if_evidence and drop_if_evidence.lower() in ev.lower():
                    dropped += 1
                    continue
                st = (r.get("state") or "").strip().upper()
                if st and st not in US_STATES:
                    skipped += 1            # a state we cannot place is not a US plant
                    continue
                out.append(contract_row(
                    source, len(out) + 1, name=name, address=(r.get("address") or ""),
                    city=(r.get("city") or ""), state=st, zip_code=(r.get("zip") or ""),
                    source_url=(r.get("source_url") or source.get("url") or ""),
                    source_document=path.name,
                    source_identifier=(r.get("registration_id") or "").strip(),
                    status=(r.get("status") or ""), expiry_date=iso_date(r.get("expires") or ""),
                    notes=ev[:400],
                    # Optional columns a transcriber may add; absent in the state-registry uploads,
                    # present in the enrichment CSV where the lookup found them.
                    website=(r.get("website") or ""), sq_ft=(r.get("sq_ft") or ""),
                    operating_status=(r.get("operating_status") or ""),
                    phone=(r.get("phone") or ""), email=(r.get("email") or ""),
                    # Coordinates only when the transcriber supplied both. They are ranked by the
                    # source's own status_basis like any other assertion, so a roster geocode of a
                    # mailing address cannot outrank the rooftop geocode stage 10 exists to produce
                    # — which matters here because BatchGeo reports accuracy=ROOFTOP on every row
                    # it returns, including the ones that are nothing of the kind.
                    lat=(r.get("lat") or "").strip(), lon=(r.get("lon") or "").strip()))
    # An empty roster is a real answer for some of these — MA publishes no list — but it must be
    # said out loud rather than read as a broken parse.
    if out:
        extra = f"{len(out)} rows"
        if dropped:
            extra += f"; {dropped} dropped as not a manufacturer"
        if skipped:
            extra += f"; {skipped} dropped for a non-US state"
        out[0]["notes"] = (out[0]["notes"] + f" | {note or source['id']}: {extra}")[:400]
    return out
