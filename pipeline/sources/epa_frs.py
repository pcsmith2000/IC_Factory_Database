"""epa_frs — EPA Facility Registry Service, national combined bulk file (class B, needs_classify).

Registry traps: 5.3M facilities, API joins return 500 — use the bulk file; NAICS 321992 is mixed;
some records are permits, not plants; OSHA-OIS programme rows are site visits (the strongest
address attestation) — flagged in `notes` as "OSHA-OIS" so reconcile/golden treat them as such.

Bulk file: https://ordsext.epa.gov/FLA/www3/state_files/national_combined.zip (~730 MB), containing
NATIONAL_FACILITY_FILE.CSV, NATIONAL_NAICS_FILE.CSV, NATIONAL_PROGRAM_FILE.CSV (+ others and the
documentation). The zip is streamed from the archive; nothing is fully loaded into memory:
  pass 1  NAICS file  → registry_ids whose NAICS is in a core code or a keyword×NAICS family prefix
                        (registry.core_naics + config.frame.naics + classify.KEYWORD_NAICS families)
  pass 2  PROGRAM file→ which of those ids carry an OSHA-OIS interest
  pass 3  FACILITY file → one contract row per kept registry_id, lat/lon carried
Layer 3 classifies what comes out; this fetcher only narrows 5.3M rows to candidates + NAICS.
The zip itself is too large to archive (pipeline/archive.py skips it, recording its sha256);
parse() writes national_combined.filtered.zip beside it — the NAICS, PROGRAM and FACILITY rows
for the kept registry ids plus SOURCE.json (url, size, sha256, date) — and that is what is archived.
Column names are those of the FRS CSV documentation (REGISTRY_ID, PRIMARY_NAME, LOCATION_ADDRESS,
CITY_NAME, STATE_CODE, POSTAL_CODE, LATITUDE83, LONGITUDE83; NAICS_CODE; PGM_SYS_ACRNM) — a
missing column raises LayoutChanged.
"""
from __future__ import annotations
import csv, io, zipfile
from pathlib import Path
from ._common import http_get, contract_row, LayoutChanged

URL = "https://ordsext.epa.gov/FLA/www3/state_files/national_combined.zip"
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


def _slice_writer(z: zipfile.ZipFile, name: str, fieldnames: list[str]):
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore"); w.writeheader()
    return buf, w


def parse(paths: list[Path], source: dict, cfg: dict | None = None) -> list[dict]:
    path = paths[0]
    core = set(source.get("core_naics") or []) | set((cfg or {}).get("frame", {}).get("naics", []))
    kept: dict[str, str] = {}   # registry_id -> naics (first kept)
    slice_path = path.with_name("national_combined.filtered.zip")
    slices: dict[str, tuple] = {}
    with zipfile.ZipFile(path) as z:
        r = _reader(z, _member(z, "NATIONAL_NAICS_FILE.CSV"))
        if not r.fieldnames or "REGISTRY_ID" not in r.fieldnames or "NAICS_CODE" not in r.fieldnames:
            raise LayoutChanged(f"NAICS file columns changed: {r.fieldnames} — inspect {path}")
        slices["NATIONAL_NAICS_FILE.CSV"] = _slice_writer(z, "naics", list(r.fieldnames))
        for row in r:
            code = (row.get("NAICS_CODE") or "").strip()
            if code in core or code[:4] in FAMILIES:
                kept.setdefault(row["REGISTRY_ID"], code)
                slices["NATIONAL_NAICS_FILE.CSV"][1].writerow(row)
        osha: set[str] = set()
        r = _reader(z, _member(z, "NATIONAL_PROGRAM_FILE.CSV"))
        slices["NATIONAL_PROGRAM_FILE.CSV"] = _slice_writer(z, "program", list(r.fieldnames or []))
        acr = next((c for c in (r.fieldnames or []) if c.upper() in ("PGM_SYS_ACRNM", "PROGRAM_SYSTEM_ACRONYM")), None)
        for row in r:
            if row["REGISTRY_ID"] in kept:
                slices["NATIONAL_PROGRAM_FILE.CSV"][1].writerow(row)
                if acr and "OSHA" in (row.get(acr) or "").upper():
                    osha.add(row["REGISTRY_ID"])
        r = _reader(z, _member(z, "NATIONAL_FACILITY_FILE.CSV"))
        need = ["REGISTRY_ID", "PRIMARY_NAME", "LOCATION_ADDRESS", "CITY_NAME", "STATE_CODE", "POSTAL_CODE"]
        if any(c not in (r.fieldnames or []) for c in need):
            raise LayoutChanged(f"facility file columns changed: {r.fieldnames} — inspect {path}")
        slices["NATIONAL_FACILITY_FILE.CSV"] = _slice_writer(z, "facility", list(r.fieldnames))
        out, pos = [], 0
        for row in r:
            pos += 1
            rid = row["REGISTRY_ID"]
            if rid not in kept:
                continue
            slices["NATIONAL_FACILITY_FILE.CSV"][1].writerow(row)
            notes = "OSHA-OIS" if rid in osha else ""
            out.append(contract_row(source, pos, name=row["PRIMARY_NAME"] or "", address=row["LOCATION_ADDRESS"] or "",
                                    city=row["CITY_NAME"] or "", state=row["STATE_CODE"] or "", zip_code=row["POSTAL_CODE"] or "",
                                    source_url=URL, source_document="NATIONAL_FACILITY_FILE.CSV", source_identifier=rid,
                                    naics=kept[rid], lat=row.get("LATITUDE83") or "", lon=row.get("LONGITUDE83") or "",
                                    status_basis="none", notes=notes, country=row.get("COUNTRY_NAME") or "US"))
    if not out:
        raise LayoutChanged(f"no facilities matched the NAICS filter ({sorted(core)} + families) — inspect {path}")
    _write_slice(path, slice_path, slices, source, len(out))
    return out


def _write_slice(path: Path, slice_path: Path, slices: dict, source: dict, n_rows: int) -> None:
    """The archivable provenance for the bulk file: the rows used, plus the identity of the zip they came from."""
    import hashlib, json
    from datetime import date
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    with zipfile.ZipFile(slice_path, "w", zipfile.ZIP_DEFLATED) as zs:
        for name, (buf, _) in slices.items():
            zs.writestr(name, buf.getvalue())
        zs.writestr("SOURCE.json", json.dumps({"url": source.get("url") or URL, "file": path.name, "bytes": path.stat().st_size,
                                               "sha256": h.hexdigest(), "retrieved": date.today().isoformat(), "rows_kept": n_rows,
                                               "note": "the full zip is not archived; these are the rows the pipeline used"}, indent=1))


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source, cfg)
