"""Assertions → golden table.

Every reconciled row becomes field-level assertions: (facility_id, field, value, source_id,
retrieved_date, row_hash, basis, confidence). Assertions are append-only. The golden table is rebuilt
from scratch every run by applying registry/survivorship.yaml — nothing writes to it directly.
A human correction is an assertion with source_id='operator' (control/operator_assertions.csv).
"""
from __future__ import annotations
import csv
from collections import defaultdict
from pathlib import Path

FIELD_MAP = {  # golden field -> contract column
    "name": "name_verbatim", "address": "address_verbatim", "city": "city_verbatim",
    "state": "state_verbatim", "zip": "zip_verbatim", "naics": "naics_verbatim",
    "status": "status_verbatim", "expiry_date": "expiry_date",
    # Optional contract columns (2026-09-17). Blank on most rows; asserted only when a source
    # printed them — GA DCA's websites, the enrichment lookups' square footage and status.
    "website": "website", "sq_ft": "sq_ft", "operating_status": "operating_status",
    "phone": "phone", "email": "email", "adl_validated": "adl_validated",
    "primary_capability": "primary_capability", "secondary_capability": "secondary_capability",
    "material": "material", "sector": "sector", "throughput": "throughput",
    "throughput_unit": "throughput_unit", "utilisation_pct": "utilisation_pct",
    "vacant_capacity": "vacant_capacity", "annual_revenue_usd": "annual_revenue_usd",
    "automation_level": "automation_level", "states_serviced": "states_serviced",
    "country_based": "country_based", "value_basis": "value_basis",
}


def assertions_from_rows(rows: list[dict], source_class: dict[str, str]) -> list[dict]:
    out = []
    for r in rows:
        base = {"facility_id": r["facility_id"], "source_id": r["source_id"], "source_class": source_class.get(r["source_id"], "?"),
                "retrieved_date": r["retrieved_date"], "row_hash": r["row_hash"], "basis": r.get("status_basis", "none"),
                "site_visit": "osha" in r["source_id"] or "OSHA" in (r.get("notes") or ""),
                "confidence": r.get("match_confidence", "")}
        for field, col in FIELD_MAP.items():
            v = (r.get(col) or "").strip()
            if v:
                out.append({**base, "field": field, "value": v})
        if r.get("lat") and r.get("lon"):
            out.append({**base, "field": "lat_lon", "value": f"{r['lat']},{r['lon']}"})
        if r.get("legal_entity_id"):
            out.append({**base, "field": "legal_name", "value": r["legal_entity_id"], "source_id": "lookup", "source_class": "lookup"})
        # The IC product category is the classifier's judgement about the row, not something the
        # source roster said, so it is asserted under its own source and keeps its own confidence.
        # Without this the column exists, is declared in survivorship.yaml, and is NULL for every
        # facility — the classifier's most useful output, computed and discarded.
        if r.get("product_type"):
            out.append({**base, "field": "product_type", "value": r["product_type"],
                        "source_id": "classifier", "source_class": "classifier",
                        "confidence": r.get("product_type_confidence", "")})
    return out


def _rank(a: dict, order: list[str]) -> int:
    for i, pref in enumerate(order):
        if pref == "site_visit" and a.get("site_visit") in (True, "True"): return i
        # A bare token names a source directly: operator, lookup, classifier. This replaces three
        # hardcoded comparisons and means a new synthetic source only has to be named in the rules.
        if not pref.startswith(("class:", "basis:")) and a["source_id"] == pref: return i
        if pref.startswith("class:") and a["source_class"] == pref[6:]: return i
        if pref.startswith("basis:") and a["basis"] == pref[6:]: return i
    return len(order)


def _recency(a: dict) -> tuple[str, str]:
    """How `tie: most_recent` orders two assertions of equal rank.

    `retrieved_date` comes first and keeps its meaning: when the SOURCE was retrieved, which is
    what should decide between two sources carrying different values. `asserted_at` — when the row
    was written — only ever breaks a tie that leaves, and that tie is not hypothetical: re-measuring
    a footprint with a corrected method produced a second assertion with the same source, basis and
    date, and max() returned whichever the sort happened to leave first. 24 facilities kept the
    superseded measurement.

    Both are ISO strings, so lexical order is chronological order, and an assertion with no
    asserted_at (every layers 1-8 assertion written before the column existed) sorts below one that
    has it — which is right: the one that recorded when it was written is the later of the two.
    """
    return (str(a.get("retrieved_date") or ""), str(a.get("asserted_at") or ""))


def build_golden(assertions: list[dict], rules: dict) -> tuple[list[dict], list[dict]]:
    """Returns (golden rows, conflicts). A conflict is a field with >1 distinct value on a facility."""
    by_fac: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for a in assertions:
        by_fac[a["facility_id"]][a["field"]].append(a)
    golden, conflicts = [], []
    for fid, fields in by_fac.items():
        g = {"facility_id": fid}
        for field, asserts in fields.items():
            order = rules["fields"].get(field, {}).get("order", rules["default_order"])
            asserts_sorted = sorted(asserts, key=lambda a: (_rank(a, order), _recency(a)), reverse=False)
            # lowest rank wins; among equal rank, most recent
            best_rank = _rank(asserts_sorted[0], order)
            tied = [a for a in asserts_sorted if _rank(a, order) == best_rank]
            win = max(tied, key=_recency)
            g[field] = win["value"]; g[f"{field}__source"] = win["source_id"]
            distinct = {a["value"] for a in asserts}
            if len(distinct) > 1:
                conflicts.append({"facility_id": fid, "field": field, "winner": win["value"], "winner_source": win["source_id"],
                                  "n_values": len(distinct), "values": " | ".join(sorted(distinct))[:300]})
        g["n_assertions"] = sum(len(v) for v in fields.values())
        g["n_sources"] = len({a["source_id"] for v in fields.values() for a in v})
        golden.append(g)
    return golden, conflicts


def load_operator_assertions(path: Path) -> list[dict]:
    """control/operator_assertions.csv: facility_id,field,value,retrieved_date,note — human corrections."""
    if not path.exists():
        return []
    out = []
    for r in csv.DictReader(open(path, newline="")):
        out.append({"facility_id": r["facility_id"], "field": r["field"], "value": r["value"], "source_id": "operator",
                    "source_class": "operator", "retrieved_date": r.get("retrieved_date", ""), "row_hash": "", "basis": "operator", "site_visit": False,
                    "confidence": 1.0})
    return out
