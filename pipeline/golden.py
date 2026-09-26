"""Assertions → golden table.

Every reconciled row becomes field-level assertions: (facility_id, field, value, source_id,
retrieved_date, row_hash, basis, confidence). Assertions are append-only. The golden table is rebuilt
from scratch every run by applying registry/survivorship.yaml — nothing writes to it directly.
A human correction is an assertion with source_id='operator' (control/operator_assertions.csv).
"""
from __future__ import annotations
import csv
import re
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


# Web research: Tako search, a manual ASTRA lookup, and the web-research agent (pipeline/web_research/
# ingest.py). Below even an unlisted source, and recency never elevates them: they fill a hole and
# never displace a roster or a geocode. A field's rule may still name one of them outright, by
# source or by basis (existence_flag names `basis:web_verdict`); that explicit rank then applies.
WEB_RESEARCH_SOURCES = ("tako_ai_search", "astra_manual_web_lookup", "web_research")


# Web research a document states literally, from a document that can vouch for it
# (pipeline/web_research/ingest.py): a registry or filing (web_verified) outranks the company's own
# site (web_primary), and both outrank every automated source. People still outrank both.
WEB_OVERRIDE_BASES = ("web_verified", "web_primary")
MONITOR_FIX = "monitor_fix"


def _rank(a: dict, order: list[str]) -> float:
    if a["source_id"] in WEB_RESEARCH_SOURCES:
        for i, pref in enumerate(order):
            if pref == a["source_id"] or (a["source_id"] == "web_research" and pref.startswith("basis:")
                                          and a.get("basis") == pref[6:]):
                return i
        if a["source_id"] == "web_research" and a.get("basis") in WEB_OVERRIDE_BASES:
            first_auto = next((i for i, p in enumerate(order) if p not in HUMAN_SOURCES + ("site_visit",)), len(order))
            # Within a basis the more veracious document wins (0.8 before 0.7), then recency.
            conf = float(a.get("confidence") or 0)
            return first_auto - 0.5 + 0.25 * WEB_OVERRIDE_BASES.index(a["basis"]) - 0.1 * conf
        return len(order) + 1
    # A monitor fix (pipeline/monitor_fix.py) sits directly below people: above a site visit and
    # above web-research overrides (first_auto - 0.6 .. - 0.25). Each line is a deliberate,
    # issue-linked correction reviewed in git; a site visit says nothing about a ZIP's format.
    if a["source_id"] == MONITOR_FIX and MONITOR_FIX not in order:
        return next((i for i, p in enumerate(order) if p not in HUMAN_SOURCES), len(order)) - 0.7
    # A street-level geocode (on the right street, not a verified building; confidence 0.3) sits
    # below everything, reviewed lookups included: it fills an empty map pin and nothing else.
    if a.get("basis") == "street_interpolated":
        return len(order) + 2
    for i, pref in enumerate(order):
        if pref == "site_visit" and a.get("site_visit") in (True, "True"): return i
        # A bare token names a source directly: operator, lookup, classifier. This replaces three
        # hardcoded comparisons and means a new synthetic source only has to be named in the rules.
        if not pref.startswith(("class:", "basis:")) and a["source_id"] == pref: return i
        if pref.startswith("class:") and a["source_class"] == pref[6:]: return i
        if pref.startswith("basis:") and a["basis"] == pref[6:]: return i
    return len(order)


def _recency(a: dict) -> tuple[str, str, str]:
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
    # Keep exact timestamp ties deterministic for confirmed feedback only; preserve
    # historical tie behavior for all existing automated/operator assertions.
    tie = str(a.get("row_hash") or "") if a.get("source_id") == "adl_employee_feedback" else ""
    return (str(a.get("retrieved_date") or ""), str(a.get("asserted_at") or ""), tie)


# Ruling a facility out of golden: existence_flag = not_ic (not an industrialized-construction plant)
# or closed (no longer operating). The facility keeps its IC-number and its assertions (ids are never
# renumbered, facts are never deleted); it just does not reach golden, so nothing that reads golden
# publishes it. A person may rule so, and so may the web-research agent with a cited source
# (pipeline/web_research/ingest.py requires one). A person outranks the agent on existence_flag, so an
# employee asserting existence_flag = active through ADL_Viz brings the plant straight back. Stage
# 12 may assert existence_flag = review and nothing else (gate E5), and review never excludes.
NOT_IC = "not_ic"
CLOSED = "closed"
EXCLUDING_FLAGS = (NOT_IC, CLOSED)
HUMAN_SOURCES = ("adl_employee_feedback", "operator")
EXCLUDING_SOURCES = HUMAN_SOURCES + ("web_research",)


def is_excluded(g: dict) -> bool:
    return g.get("existence_flag") in EXCLUDING_FLAGS and g.get("existence_flag__source") in EXCLUDING_SOURCES


def split_excluded(golden: list[dict], conflicts: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(kept golden rows, their conflicts, the excluded golden rows)."""
    dropped = [g for g in golden if is_excluded(g)]
    ids = {g["facility_id"] for g in dropped}
    return ([g for g in golden if g["facility_id"] not in ids],
            [c for c in conflicts if c["facility_id"] not in ids], dropped)


def build_golden(assertions: list[dict], rules: dict, *, keep_excluded: bool = False) -> tuple[list[dict], list[dict]]:
    """Returns (golden rows, conflicts). A conflict is a field with >1 distinct value on a facility.

    A facility a person has ruled not IC is left out unless `keep_excluded` — a caller that has to
    account for what was dropped builds with it and splits with split_excluded."""
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
        for field, rule in (rules.get("derived") or {}).items():
            pool = [a for src in rule.get("from", []) for a in fields.get(src, []) if _plausible_number(a.get("value"), rule)]
            if not pool:
                continue
            order = rule.get("order", rules["default_order"])
            best_rank = min(_rank(a, order) for a in pool)
            win = max((a for a in pool if _rank(a, order) == best_rank), key=_recency)
            g[field] = _plausible_number(win["value"], rule); g[f"{field}__source"] = win["source_id"]
        g["n_assertions"] = sum(len(v) for v in fields.values())
        g["n_sources"] = len({a["source_id"] for v in fields.values() for a in v})
        golden.append(g)
    if not keep_excluded:
        golden, conflicts, _ = split_excluded(golden, conflicts)
    return golden, conflicts


def _plausible_number(value, rule: dict) -> str | None:
    """The value as a canonical integer string when it reads as a number inside the rule's
    [min, max]; None otherwise. '120,000 sq ft' and '120000.0' both read as 120000."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(value or ""))
    if not m:
        return None
    try:
        n = float(m.group().replace(",", ""))
    except ValueError:
        return None
    if not (float(rule.get("min", 0)) <= n <= float(rule.get("max", float("inf")))):
        return None
    return str(int(round(n)))


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
