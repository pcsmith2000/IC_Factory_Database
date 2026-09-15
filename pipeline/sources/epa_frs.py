"""epa_frs — EPA Facility Registry Service, national combined bulk file (class B, needs_classify).

Registry traps: 5.3M facilities, API joins return 500 — use the bulk file; NAICS 321992 is mixed;
some records are permits, not plants; OSHA-OIS programme rows are site visits (the strongest
address attestation) — flagged in `notes` as "OSHA-OIS" so reconcile/golden treat them as such.

Bulk file: https://ofmext.epa.gov/FLA/www3/state_files/national_combined.zip (~730 MB), containing
NATIONAL_FACILITY_FILE.CSV, NATIONAL_NAICS_FILE.CSV, NATIONAL_PROGRAM_FILE.CSV (+ others and the
documentation). The zip is streamed from the archive; nothing is fully loaded into memory:
  pass 1  NAICS file  → registry_ids whose NAICS is in a core code or a keyword×NAICS family prefix
                        (registry.core_naics + config.frame.naics + classify.KEYWORD_NAICS families)
  pass 2  PROGRAM file→ which of those ids carry an OSHA-OIS interest
  pass 3  FACILITY file → one contract row per kept registry_id, lat/lon carried
Layer 3 classifies what comes out; this fetcher only narrows 5.3M rows to candidates + NAICS.
Column names are those of the FRS CSV documentation (REGISTRY_ID, PRIMARY_NAME, LOCATION_ADDRESS,
CITY_NAME, STATE_CODE, POSTAL_CODE, LATITUDE83, LONGITUDE83; NAICS_CODE; PGM_SYS_ACRNM) — a
missing column raises LayoutChanged.
"""
from __future__ import annotations
import csv, io, zipfile
from pathlib import Path
from ._common import http_get, contract_row, LayoutChanged

URL = "https://ofmext.epa.gov/FLA/www3/state_files/national_combined.zip"
FAMILIES = {"3219", "3212", "3323", "2362", "2381", "4233", "3273", "3272", "3211"}  # classify.KEYWORD_NAICS values


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    return [http_get(source.get("url") or URL, archive_dir, "national_combined.zip", timeout=3600)]


def _member(z: zipfile.ZipFile, name: str) -> str:
    for n in z.namelist():
        if n.upper().endswith(name.upper()):
            return n
    raise LayoutChanged(f"{name} not in the FRS zip (members: {z.namelist()[:12]}...)")


def _reader(z: zipfile.ZipFile, member: str):
    return csv.DictReader(io.TextIOWrapper(z.open(member), encoding="utf-8", errors="replace", newline=""))


def parse(paths: list[Path], source: dict, cfg: dict | None = None) -> list[dict]:
    path = paths[0]
    core = set(source.get("core_naics") or []) | set((cfg or {}).get("frame", {}).get("naics", []))
    kept: dict[str, str] = {}   # registry_id -> naics (first kept)
    with zipfile.ZipFile(path) as z:
        r = _reader(z, _member(z, "NATIONAL_NAICS_FILE.CSV"))
        if not r.fieldnames or "REGISTRY_ID" not in r.fieldnames or "NAICS_CODE" not in r.fieldnames:
            raise LayoutChanged(f"NAICS file columns changed: {r.fieldnames} — inspect {path}")
        for row in r:
            code = (row.get("NAICS_CODE") or "").strip()
            if code in core or code[:4] in FAMILIES:
                kept.setdefault(row["REGISTRY_ID"], code)
        osha: set[str] = set()
        r = _reader(z, _member(z, "NATIONAL_PROGRAM_FILE.CSV"))
        acr = next((c for c in (r.fieldnames or []) if c.upper() in ("PGM_SYS_ACRNM", "PROGRAM_SYSTEM_ACRONYM")), None)
        if acr:
            for row in r:
                if row["REGISTRY_ID"] in kept and "OSHA" in (row.get(acr) or "").upper():
                    osha.add(row["REGISTRY_ID"])
        r = _reader(z, _member(z, "NATIONAL_FACILITY_FILE.CSV"))
        need = ["REGISTRY_ID", "PRIMARY_NAME", "LOCATION_ADDRESS", "CITY_NAME", "STATE_CODE", "POSTAL_CODE"]
        if any(c not in (r.fieldnames or []) for c in need):
            raise LayoutChanged(f"facility file columns changed: {r.fieldnames} — inspect {path}")
        out, pos = [], 0
        for row in r:
            pos += 1
            rid = row["REGISTRY_ID"]
            if rid not in kept:
                continue
            notes = "OSHA-OIS" if rid in osha else ""
            out.append(contract_row(source, pos, name=row["PRIMARY_NAME"] or "", address=row["LOCATION_ADDRESS"] or "",
                                    city=row["CITY_NAME"] or "", state=row["STATE_CODE"] or "", zip_code=row["POSTAL_CODE"] or "",
                                    source_url=URL, source_document="NATIONAL_FACILITY_FILE.CSV", source_identifier=rid,
                                    naics=kept[rid], lat=row.get("LATITUDE83") or "", lon=row.get("LONGITUDE83") or "",
                                    status_basis="none", notes=notes, country=row.get("COUNTRY_NAME") or "US"))
    if not out:
        raise LayoutChanged(f"no facilities matched the NAICS filter ({sorted(core)} + families) — inspect {path}")
    return out


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source, cfg)
