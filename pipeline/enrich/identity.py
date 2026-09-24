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


def _identity_by_release(assertions: list[dict]) -> dict[tuple[str, str], set[tuple[str, str, str]]]:
    """(facility_id, release) -> every (name, city, state) that release asserted for the id,
    normalised. Several sources may spell the name or the city differently in one release
    ("Clayton Homes" / "CLAYTON HOMES INC", "Ft Worth" / "Fort Worth"); each combination is a key,
    and two releases name the same plant when any key is shared."""
    parts: dict[tuple[str, str], dict[str, set[str]]] = {}
    for a in assertions:
        f = a.get("field")
        if f in ("name", "city", "state") and _norm(a.get("value")):
            parts.setdefault((a["facility_id"], a.get("release_tag") or ""), {}).setdefault(f, set()).add(_norm(a["value"]))
    out = {}
    for key, fields in parts.items():
        if "name" in fields:
            out[key] = {(n, c, s) for n in fields["name"] for c in (fields.get("city") or {""}) for s in (fields.get("state") or {""})}
    return out


# Facts that belong to the street address rather than to the business name: a coordinate, a
# footprint measured at it, the existence verdict drawn from that footprint, the geocode's grade.
ADDRESS_BOUND_FIELDS = ("lat_lon", "building_sqft", "existence_flag", "geocode_quality")


def _address_by_release(rows: list[dict]) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """(facility_id, release) -> {(normalised numbered street address, state)}."""
    parts: dict[tuple[str, str], dict[str, set[str]]] = {}
    for a in rows:
        f = a.get("field")
        if f == "address" and re.match(r"^\s*\d", str(a.get("value") or "")) and _norm(a.get("value")):
            parts.setdefault((a["facility_id"], a.get("release_tag") or ""), {}).setdefault("address", set()).add(_norm(a["value"]))
        elif f == "state" and _norm(a.get("value")):
            parts.setdefault((a["facility_id"], a.get("release_tag") or ""), {}).setdefault("state", set()).add(_norm(a["value"]))
    return {key: {(ad, st) for ad in fields["address"] for st in fields.get("state", {""})}
            for key, fields in parts.items() if "address" in fields}


def carry_by_identity(assertions: list[dict], release_tag: str, identity_rows: list[dict] | None = None) -> tuple[list[dict], list[dict], dict]:
    """Rebuild the cross-release carry-over on identity rather than on id.

    Every assertion from the current release is kept as is. An assertion from another release
    is re-keyed to the CURRENT facility that shares a (name, city, state) with what its own
    release asserted for its id — the plant it was actually about — and dropped when no current
    facility or more than one does. The assertion rows in the warehouse are untouched; only the
    facility they are read under changes, and each re-key is reported.

    `identity_rows` are the name, city and state rows of the other releases (_db.fetch_identity_rows):
    the carried enrichment alone carries no name, so without them every carried assertion would
    be withheld as coming from a release that asserted no identity — which is what happened on
    2026-09-23, when 14,509 footprints, existence flags and coordinates were withheld at once.
    """
    ident = _identity_by_release(list(assertions) + list(identity_rows or []))
    current: dict[tuple[str, str, str], set[str]] = {}
    by_name: dict[str, set[str]] = {}          # every current facility under each of its names
    nameless: dict[str, set[str]] = {}         # current facilities that assert a name but no city and no state
    for (fid, tag), keys in ident.items():
        if tag == release_tag:
            for key in keys:
                current.setdefault(key, set()).add(fid)
                by_name.setdefault(key[0], set()).add(fid)
                if key[1] == "" and key[2] == "":
                    nameless.setdefault(key[0], set()).add(fid)
    addresses = _address_by_release(list(assertions) + list(identity_rows or []))
    current_by_address: dict[tuple[str, str], set[str]] = {}
    for (fid, tag), keys in addresses.items():
        if tag == release_tag:
            for key in keys:
                current_by_address.setdefault(key, set()).add(fid)
    kept, withheld = [], []
    rekeyed = 0
    same_plant = 0
    by_unique_name = 0
    by_address = 0
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
        targets: set[str] = set()
        for key in then:
            targets |= current.get(key, set())
        unique_name = False
        by_addr = False
        if not targets:
            # The current release knows the plant by name alone — no city, no state — and that name
            # belongs to exactly one current facility. Nothing contradicts the old release's locality,
            # and a unique name is the strongest signal left; the state gate still judges the point.
            for name in {k[0] for k in then}:
                if name in nameless and len(by_name.get(name, ())) == 1:
                    targets |= nameless[name]
                    unique_name = True
        if not targets and a.get("field") in ADDRESS_BOUND_FIELDS:
            # A numbered street address in a state is one parcel. A coordinate (or a footprint, or
            # the verdict drawn from it) found for that address belongs to whichever current facility
            # stands at it, whatever the business is now called — when exactly one does. The name
            # is asked first: a plant known by its unique name keeps its own coordinate even when a
            # second current row (a duplicate with a locality) stands at the same address.
            for key in addresses.get((a["facility_id"], tag), ()):
                if len(current_by_address.get(key, ())) == 1:
                    targets |= current_by_address[key]
                    by_addr = True
            if len(targets) > 1:
                targets = set()
                by_addr = False
        if len(targets) > 1 and a["facility_id"] in targets:
            targets = {a["facility_id"]}      # duplicates in the current release: the id it was written under wins
        if len(targets) != 1:
            withheld.append({"facility_id": a["facility_id"], "field": a.get("field"), "value": a.get("value"),
                             "source": a.get("source_id"), "release_tag": tag,
                             "reason": "identity_absent_from_current_release" if not targets else "identity_ambiguous_in_current_release",
                             "identity_then": sorted(min(then))})
            continue
        target = next(iter(targets))
        how = "unique_name" if unique_name else "address" if by_addr else None
        if unique_name:
            by_unique_name += 1
        if by_addr:
            by_address += 1
        if target == a["facility_id"]:
            same_plant += 1
            kept.append(a if how is None else {**a, "carried_by": how})
        else:
            rekeyed += 1
            kept.append({**a, "facility_id": target, "carried_from_facility_id": a["facility_id"],
                         **({"carried_by": how} if how else {})})
    return kept, withheld, {"carried_same_id_same_plant": same_plant, "carried_rekeyed_by_identity": rekeyed,
                            "carried_by_unique_name": by_unique_name, "carried_by_address": by_address}
