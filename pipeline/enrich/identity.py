"""Gate E11 — an assertion carried across releases must belong to the same plant.

fact_assertions is append-only across releases, and promote carries enrichment (and reviewed
web-lookup) assertions forward by facility id so paid work survives a new release. That is safe
only while an id names one plant. Releases built on diverging id registries broke it: IC-95293 was
Borntreger Truss (WI), then Blueprint Robotics Baltimore (MD), then Bankersteel South Plainfield
(NJ), then Ladabuild (CO), and the address and rooftop researched for Blueprint Robotics were
carried onto Ladabuild. The state-box gate (E10) catches the pin; this catches the address, the
website and everything else, and it does so exactly rather than geographically.

The rule: for a facility, the names asserted in the CURRENT release define the plant. A carried
assertion written under another release is kept only when that release's names for the facility
overlap the current ones. A release with no name assertion for the facility cannot be judged and
is kept, counted separately.
"""
from __future__ import annotations
import re


def _norm(v) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def names_by_release(assertions: list[dict]) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for a in assertions:
        if a.get("field") == "name" and _norm(a.get("value")):
            out.setdefault((a["facility_id"], a.get("release_tag") or ""), set()).add(_norm(a["value"]))
    return out


def split_carried(assertions: list[dict], release_tag: str) -> tuple[list[dict], list[dict], int]:
    """(kept, withheld, unjudged). Withheld assertions name a different plant than the current
    release does under the same id; unjudged ones come from a release that asserted no name."""
    names = names_by_release(assertions)
    kept, withheld, unjudged = [], [], 0
    for a in assertions:
        tag = a.get("release_tag") or ""
        if tag == release_tag or not tag:
            kept.append(a)
            continue
        current = names.get((a["facility_id"], release_tag), set())
        then = names.get((a["facility_id"], tag), set())
        if not then or not current:
            unjudged += 1
            kept.append(a)
        elif current & then:
            kept.append(a)
        else:
            withheld.append({"facility_id": a["facility_id"], "field": a.get("field"), "value": a.get("value"),
                             "source": a.get("source_id"), "release_tag": tag,
                             "named_then": sorted(then)[:3], "named_now": sorted(current)[:3]})
    return kept, withheld, unjudged


def _identity_by_release(assertions: list[dict]) -> dict[tuple[str, str], tuple[str, str, str]]:
    """(facility_id, release) -> (name, city, state) as that release asserted them, normalised.
    Several sources may assert a name in one release; the alphabetically first normalised value
    is taken so the key is deterministic."""
    parts: dict[tuple[str, str], dict[str, set[str]]] = {}
    for a in assertions:
        f = a.get("field")
        if f in ("name", "city", "state") and _norm(a.get("value")):
            parts.setdefault((a["facility_id"], a.get("release_tag") or ""), {}).setdefault(f, set()).add(_norm(a["value"]))
    out = {}
    for key, fields in parts.items():
        if "name" in fields:
            out[key] = (min(fields["name"]), min(fields.get("city") or {""}), min(fields.get("state") or {""}))
    return out


def carry_by_identity(assertions: list[dict], release_tag: str) -> tuple[list[dict], list[dict], dict]:
    """Rebuild the cross-release carry-over on identity rather than on id.

    Every assertion from the current release is kept as is. An assertion from another release
    is re-keyed to the CURRENT facility whose (name, city, state) equals what its own release
    asserted for its id — the plant it was actually about — and dropped when no current facility
    or more than one carries that identity. The assertion rows in the warehouse are untouched;
    only the facility they are read under changes, and each re-key is reported.
    """
    ident = _identity_by_release(assertions)
    current: dict[tuple[str, str, str], list[str]] = {}
    for (fid, tag), key in ident.items():
        if tag == release_tag:
            current.setdefault(key, []).append(fid)
    kept, withheld = [], []
    rekeyed = 0
    same_plant = 0
    for a in assertions:
        tag = a.get("release_tag") or ""
        if tag == release_tag or not tag:
            kept.append(a)
            continue
        then = ident.get((a["facility_id"], tag))
        if then is None:
            withheld.append({"facility_id": a["facility_id"], "field": a.get("field"), "value": a.get("value"),
                             "source": a.get("source_id"), "release_tag": tag, "reason": "no_identity_in_that_release"})
            continue
        targets = current.get(then, [])
        if len(targets) != 1:
            withheld.append({"facility_id": a["facility_id"], "field": a.get("field"), "value": a.get("value"),
                             "source": a.get("source_id"), "release_tag": tag,
                             "reason": "identity_absent_from_current_release" if not targets else "identity_ambiguous_in_current_release",
                             "identity_then": list(then)})
            continue
        target = targets[0]
        if target == a["facility_id"]:
            same_plant += 1
            kept.append(a)
        else:
            rekeyed += 1
            kept.append({**a, "facility_id": target, "carried_from_facility_id": a["facility_id"]})
    return kept, withheld, {"carried_same_id_same_plant": same_plant, "carried_rekeyed_by_identity": rekeyed}
