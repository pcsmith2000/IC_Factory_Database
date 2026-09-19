"""Shared reader for the 2026-09-18 evidence collection: five certification directories and OSHA.

All of it arrives in one schema (record_id, company_name, entity_type, street_address, city,
state_province, postal_code, country, latitude, longitude, website, phone, product_types,
certification_program, certification_categories, source_status, certificate_expires,
inspection_date, naics_code, ic_scope, inclusion_basis, source_url, source_detail_url,
data_quality_notes), so one reader serves both sources. The collection ships a file_manifest.json
of SHA-256 hashes; all eight files verified on arrival.

Its own README is the best documentation any source in this pipeline has, and the rules below come
from it rather than from guessing:

  * "Use ic_scope and entity_type to distinguish building-component fabrication, building
    materials, infrastructure, vendor organizations and industry leads. Values are derived triage
    categories, not authoritative product certifications."
  * "NFBA roles are the directory's classifications. Supplier does not mean factory; builder does
    not mean manufacturer."
  * "AISC covers the US directory, including erectors and infrastructure fabricators."
  * "Current directory listing is not an independent verification of production capacity or
    factory operation."
  * "Inspection dates describe case opening, not proof of current operation."

So a row is kept only when the source's own entity_type names something that MAKES things. An
erector erects, a builder builds, a supplier supplies, a designer designs — the classifier prompt
has said exactly this since v1.0 ("Erectors and installers ... do not manufacture them") and the
directories agree in their own vocabulary. 1,093 of the 2,778 US directory rows are one of those.
Keeping them would publish 537 steel erectors as factories.

What is NOT thrown away is the evidence for the judgement: entity_type, ic_scope, product_types,
the certification programme and its categories, the source's status and the collector's own
data_quality_notes all ride into `notes`, so a row that looks wrong can be argued with.
"""
from __future__ import annotations
import csv
from pathlib import Path
from ._common import US_STATES, contract_row, iso_date, pick, require

# entity_type values that denote making something. Everything else the directories list —
# erector, builder, supplier, designer, accredited_facility — is in the book as not a plant.
MAKERS = {"fabricator", "plant", "mill", "manufacturer", "producer", "inspection_record"}

# ic_scope values that are about buildings. infrastructure_only is bridges and highway work.
OUT_OF_SCOPE = {"vendor_ecosystem", "infrastructure_only"}


def _note(r: dict) -> str:
    bits = [f"{k}: {r[k].strip()}" for k in
            ("entity_type", "ic_scope", "product_types", "certification_program",
             "certification_categories", "source_status", "inclusion_basis",
             "inspection_date", "data_quality_notes")
            if (r.get(k) or "").strip()]
    return " | ".join(bits)[:400]


def read(paths: list[Path], source: dict) -> list[dict]:
    files = pick(paths, ".csv")
    require(bool(files), paths[0] if paths else Path(source["id"]),
            "no CSV in the archived folder — upload the evidence file there, then re-run")
    out, non_us, not_maker, out_scope, no_name = [], 0, 0, 0, 0
    for path in sorted(files):
        for r in csv.DictReader(open(path, newline="", encoding="utf-8-sig", errors="replace")):
            name = (r.get("company_name") or "").strip()
            if not name:
                no_name += 1; continue
            if (r.get("country") or "").strip() not in ("United States", "US", "USA", ""):
                non_us += 1; continue
            st = (r.get("state_province") or "").strip().upper()
            if st and st not in US_STATES:
                non_us += 1; continue
            if (r.get("entity_type") or "").strip() not in MAKERS:
                not_maker += 1; continue          # an erector is not a factory; the README agrees
            if (r.get("ic_scope") or "").strip() in OUT_OF_SCOPE:
                out_scope += 1; continue
            out.append(contract_row(
                source, len(out) + 1, name=name,
                address=(r.get("street_address") or ""), city=(r.get("city") or ""), state=st,
                zip_code=(r.get("postal_code") or "")[:10],
                naics=(r.get("naics_code") or "").strip(),
                status=(r.get("source_status") or ""),
                expiry_date=iso_date(r.get("certificate_expires") or ""),
                lat=(r.get("latitude") or "").strip(), lon=(r.get("longitude") or "").strip(),
                phone=(r.get("phone") or ""), website=(r.get("website") or ""),
                source_url=(r.get("source_detail_url") or r.get("source_url") or source.get("url", "")),
                source_document=path.name,
                source_identifier=(r.get("record_id") or r.get("source_record_id") or "").strip(),
                notes=_note(r)))
    require(bool(out), files[0], "CSV read but every row was dropped — check entity_type/ic_scope")
    out[0]["notes"] = (out[0]["notes"] + f" || {source['id']}: {len(out)} kept; {not_maker} not a "
                       f"maker (erector/builder/supplier/designer), {out_scope} out of scope "
                       f"(vendor ecosystem or infrastructure only), {non_us} non-US, "
                       f"{no_name} unnamed")[:400]
    return out
