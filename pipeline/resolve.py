"""Layer 4: entity resolution — every row to a legal entity BEFORE any matching.

NOT YET BUILT. Deterministic lookups first (state SoS, FMCSA SAFER, ICC-ES/IAS, explicit
crosswalk ids); a model adjudicates only residual name-variant matches with a logged reason.
A row that cannot be resolved keeps its trading name and gets RESOLVE-FAILED — never a guess.

Until the lookups exist this layer passes rows through with `legal_entity_id = ""` and
`resolve_status = "NOT-ATTEMPTED"`, and the run record reports resolution_rate = 0.0 so the
gap is visible in every release rather than hidden.
"""
from __future__ import annotations


def run(rows: list[dict], crosswalk: dict[str, str] | None = None) -> dict:
    crosswalk = crosswalk or {}
    n_cross = 0
    for r in rows:
        cw = crosswalk.get(r["row_hash"])
        if cw:
            r["legal_entity_id"] = cw; r["resolve_status"] = "CROSSWALK"; n_cross += 1
        else:
            r["legal_entity_id"] = ""; r["resolve_status"] = "NOT-ATTEMPTED"
    return {"rows": len(rows), "resolved_by_crosswalk": n_cross,
            "resolution_rate": (n_cross / len(rows)) if rows else 0.0, "layer_built": False}
