"""Shared reader for the two 2026-09-18 evidence collections.

The first is five certification directories plus OSHA (`ic_directories`, `osha_inspections`); the
second is 26 state registries and certification programmes (`ic_directories_more`). Both arrive in
one schema — record_id, source, company_name, entity_type, street_address, city, state_province,
postal_code, country, latitude, longitude, website, phone, product_types, certification_program,
certification_categories, source_status, certificate_expires, inspection_date, naics_code,
ic_scope, inclusion_basis, source_url, source_detail_url, data_quality_notes — the second adding
source_as_of, approval_jurisdiction, address_type, mailing_address, evaluation_agency,
inspection_agency, evidence_text and collection_notes. So one reader serves all of it.

Their READMEs are the best documentation any source in this pipeline has, and the rules below come
from them rather than from guessing:

  * "Use ic_scope and entity_type to distinguish building-component fabrication, building
    materials, infrastructure, vendor organizations and industry leads. Values are derived triage
    categories, not authoritative product certifications."
  * "NFBA roles are the directory's classifications. Supplier does not mean factory; builder does
    not mean manufacturer."
  * "AISC covers the US directory, including erectors and infrastructure fabricators."
  * "Current directory listing is not an independent verification of production capacity or
    factory operation."
  * "Inspection dates describe case opening, not proof of current operation."
  * "Directory includes many distribution branches and contractors. Do not treat branch as
    manufacturing plant without supporting profile evidence."

So a row is kept only when the source's own entity_type names something that MAKES things. An
erector erects, a builder builds, a supplier supplies, a designer designs — the classifier prompt
has said exactly this since v1.0 ("Erectors and installers ... do not manufacture them") and the
directories agree in their own vocabulary. 1,093 of the first collection's 2,778 US rows are one
of those; keeping them would have published 537 steel erectors as factories.

Where the two collections differ is how far the collector got. The first is triaged and its
ic_scope can be trusted to filter. The second is not — 4,124 of its 5,343 rows are flagged for
review — so its rows are admitted on entity_type and the CLASSIFIER decides, which is what
shipping them unreviewed asks for.

What is NOT thrown away either way is the evidence for the judgement: the programme, entity_type,
ic_scope, address_type, product_types, the certification categories, the source's status, its
as-of date, the evaluating and inspecting agencies, the collector's evidence_text and both sets of
notes all ride into `notes`, so a row that looks wrong can be argued with.
"""
from __future__ import annotations
import csv
from pathlib import Path
from ._common import US_STATES, contract_row, iso_date, pick, require

# Which rows are FACTORY candidates, decided from the source's own entity_type.
#
# The first 2026-09-18 collection speaks in single words — fabricator, plant, mill, manufacturer,
# producer against erector, builder, supplier, designer, accredited_facility — and its README is
# blunt: "Supplier does not mean factory; builder does not mean manufacturer." Keeping the second
# group would have published 537 steel erectors as plants.
#
# The ADDITIONAL collection speaks in phrases instead, and there are twenty-five of them:
# active_prefab_registration, valid_manufacturer_license, approved_fabrication_site,
# certified_plant, "Builder, Dealer/Distributor, Design Professional, Manufacturing". An exact
# list cannot keep up with that and fails SILENTLY — a value nobody thought of is simply dropped,
# which is how 1,133 LADBS fabricator licences would have vanished without a word. So the test is
# on the WORDS the label is built from. Every refusal below is still a refusal: erector, builder,
# supplier, designer, distributor and accredited_facility contain none of these stems.
MAKER_WORDS = ("manufactur", "fabricat", "producer", "plant", "mill", "precast", "truss",
               "prefab", "client_listing", "product_certification", "member_location",
               "inspection_record")

# What is deliberately NOT a maker word is `provider_branch_location`, BuildSteel's 1,218 branch
# rows — because the collector's own note on them is "Directory includes many distribution
# branches and contractors. Do not treat branch as manufacturing plant without supporting profile
# evidence", and the file proves the warning: 366 of those branches are AD Gypsum Supply, 264 are
# L&W Supply and 262 are Gypsum Management — 892 building-materials distribution yards. Neither
# the file nor the classifier can tell a ClarkDietrich roll-forming plant from a gypsum yard
# reading a branch address alone, so BuildSteel waits for the role triage the collector asked for.
#
# `Associate` is refused outright whatever else a row says. An association's associate membership
# is a supplier or a consultant by definition and NPCA says so in its own directory — 345 rows,
# every one of them scoped vendor_ecosystem by the collector too.
NOT_MAKERS = {"associate"}

# ic_scope values that are not a building factory, in the collector's own vocabulary.
#   vendor_ecosystem                      suppliers and consultants around the industry
#   infrastructure_only / _or_other       bridges and highway work
#   outside_core_scope_ready_mix          ready-mix concrete is not industrialised construction
#   project_specific_not_factory_evidence an approval for ONE job, at a jobsite address
#   electrical_features_only              a listing about electrical features, not a plant
# What is NOT refused here is the untriaged half of the additional collection — needs_review,
# needs_role_review, fabrication_scope_review, needs_product_review, 4,124 rows. The collector
# shipped those deliberately unreviewed, which is a request for the classifier to read them, not
# a reason to drop them unread.
OUT_OF_SCOPE = {"vendor_ecosystem", "infrastructure_only", "infrastructure_or_other",
                "outside_core_scope_ready_mix", "project_specific_not_factory_evidence",
                "electrical_features_only"}


def is_maker(r: dict) -> bool:
    et = (r.get("entity_type") or "").strip().lower()
    if not et or et in NOT_MAKERS:
        return False
    if any(w in et for w in MAKER_WORDS):
        return True
    # One named exception, because a label should not outrank the certificate it describes. IAS
    # lists AC473 facilities as `accredited_facility`, the same word the first collection uses for
    # AC472 post-frame BUILDERS — but AC473 is "Manufacturers of Cold-Formed Steel Components",
    # which is a plant by definition. Matching on the programme code keeps that narrow: it admits
    # 25 rows and cannot reach anything in the first collection.
    return "ac473" in (r.get("certification_program") or "").lower()


def _note(r: dict) -> str:
    bits = [f"{k}: {r[k].strip()}" for k in
            ("source", "entity_type", "ic_scope", "address_type", "approval_jurisdiction",
             "product_types", "certification_program", "certification_categories",
             "source_status", "inclusion_basis", "inspection_date", "source_as_of",
             "evaluation_agency", "inspection_agency", "evidence_text", "data_quality_notes",
             "collection_notes")
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
            if not is_maker(r):
                not_maker += 1; continue          # an erector is not a factory; the README agrees
            if (r.get("ic_scope") or "").strip() in OUT_OF_SCOPE:
                out_scope += 1; continue
            out.append(contract_row(
                source, len(out) + 1, name=name,
                # address_type is the collection's own label for what this address IS. A row
                # flagged city_only carries no street at all, and one flagged as a branch or
                # "not verified as factory" carries its street with that caveat in the notes —
                # the street is still the best locator anyone has for it.
                address=("" if "city_only" in (r.get("address_type") or "")
                         else (r.get("street_address") or "")),
                city=(r.get("city") or ""), state=st,
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
