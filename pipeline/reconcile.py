"""Layer 5: cluster rows into facilities and issue stable IC-numbers.

Blocking key: state + (legal_entity_id if resolved) + street_key when present, else
state + city_norm + normalised name. Every match records method and confidence.
Stable ids come from id_registry.json: an existing cluster signature keeps its id; a new
signature gets the next id; nothing is ever renumbered (gate G3 tests exactly this).

The production reconcile.py from the process record replaces the clustering here when the
scripts are added; the id-registry contract must be kept.
"""
from __future__ import annotations
import json, re
from collections import defaultdict
from pathlib import Path

TIER_RULES = "T0 never counted · T1 one source with plant address · T2 two independent sources agree · T3 site-visit or geocoded rooftop · T4 operator-confirmed"


def norm_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    s = re.sub(r"\b(inc|llc|l l c|corp|corporation|co|company|ltd|the)\b", " ", s)
    return " ".join(s.split())


def signature(r: dict) -> tuple[str, str]:
    st = r.get("state", "")
    if r.get("legal_entity_id") and r.get("street_key"):
        return (f"{st}|E|{r['legal_entity_id']}|{r['street_key']}", "entity+street")
    if r.get("street_key"):
        return (f"{st}|S|{r['street_key']}", "street_key")
    return (f"{st}|N|{r.get('city_norm','')}|{norm_name(r.get('name_verbatim',''))}", "name+city")


class IdRegistry:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {"next": 1, "ids": {}}
        self.issued_this_run = 0

    def get(self, sig: str) -> str:
        if sig not in self.data["ids"]:
            self.data["ids"][sig] = f"IC-{self.data['next']:05d}"
            self.data["next"] += 1
            self.issued_this_run += 1
        return self.data["ids"][sig]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, sort_keys=True))


def tier(cluster: list[dict]) -> str:
    sources = {r["source_id"] for r in cluster}
    has_addr = any(r.get("street_key") for r in cluster)
    site_visit = any("osha" in (r.get("source_id") or "") or "OSHA" in (r.get("notes") or "") for r in cluster)
    if not has_addr:
        return "T0"
    if site_visit and len(sources) >= 2:
        return "T3"
    if len(sources) >= 2:
        return "T2"
    return "T1"


def _attach_addressless(clusters: dict[str, list[dict]], methods: dict[str, str]) -> int:
    """Fold a name+city cluster into the addressed cluster it plainly belongs to.

    Rosters disagree about addresses: one carries a street, another only city and state, and the
    same plant then takes two ids because the signatures differ. That is the single largest source
    of duplication — 76 of the 320 pairs G1 raised on 2026-09-17 were this exact shape.

    The merge is deliberately timid. An addressless cluster joins an addressed one only when state,
    normalised city and normalised name all agree AND exactly one addressed cluster matches. Two
    candidates means we cannot tell which plant the roster meant, so it stays separate — a company
    with several plants in one city is common enough that guessing would manufacture false merges,
    which is the failure G2 exists to catch and is far worse than a duplicate.

    Ids stay stable: the addressed cluster keeps its signature and therefore its id, and the
    addressless signature is simply retired. Nothing is renumbered and no id is issued, so G3 is
    unaffected.
    """
    addressed: dict[tuple, list[str]] = defaultdict(list)
    for sig, members in clusters.items():
        if methods[sig] in ("street_key", "entity+street"):
            f = members[0]
            key = (f.get("state", ""), f.get("city_norm", ""), norm_name(f.get("name_verbatim", "")))
            if all(key):
                addressed[key].append(sig)
    folded = 0
    for sig in [s for s, m in methods.items() if m == "name+city"]:
        f = clusters[sig][0]
        key = (f.get("state", ""), f.get("city_norm", ""), norm_name(f.get("name_verbatim", "")))
        targets = addressed.get(key, [])
        if len(targets) != 1:
            continue                      # unknown plant, or none — leave it alone
        target = targets[0]
        for r in clusters[sig]:
            r["attached_from"] = "name+city"
        clusters[target].extend(clusters.pop(sig))
        methods.pop(sig, None)
        folded += 1
    return folded


def run(rows: list[dict], registry_path: Path) -> dict:
    reg = IdRegistry(registry_path)
    clusters: dict[str, list[dict]] = defaultdict(list)
    methods: dict[str, str] = {}
    for r in rows:
        if r.get("no_fixed_plant"):
            sig, m = (f"NFP|{norm_name(r.get('name_verbatim',''))}", "no-fixed-plant")
        else:
            sig, m = signature(r)
        clusters[sig].append(r); methods[sig] = m
    _attach_addressless(clusters, methods)
    facilities = []
    for sig, members in clusters.items():
        fid = reg.get(sig)
        for r in members:
            # A row folded in by _attach_addressless was matched on name and city, NOT on the
            # street key that gives its new cluster an id. Recording the cluster's method here
            # would claim 0.90 street-level provenance for a row that never carried a street, and
            # v_provenance would repeat that claim to anyone tracing the value back.
            m = "name+city→addressed" if r.get("attached_from") else methods[sig]
            r["facility_id"] = fid; r["match_method"] = m
            r["match_confidence"] = {"entity+street": 0.95, "street_key": 0.90, "name+city": 0.70,
                                     "name+city→addressed": 0.70, "no-fixed-plant": 0.60}[m]
        first = members[0]
        # Every OTHER name the sources gave this plant, kept rather than discarded. A cluster takes
        # one name — whichever row sorted first — and the rest were being thrown away: 774 of them
        # across 4,204 facilities. That loss is not cosmetic. IC-93899 is Premier SIPS at 18504
        # Canyon Rd E, Puyallup WA, merged from or_bcd and sipa on street_key; it publishes as
        # "PREMIER BUILDING SYSTEMS" because or_bcd sorted first, and the control list writes
        # "PREMIER SIPS", so Layer 7 counted a plant we hold in two sources as one we do not hold
        # at all. Keeping the aliases is recovering data we already fetched, not loosening a match:
        # each alias is still compared by the same exact and whole-word-prefix rungs.
        primary = first.get("name_verbatim") or ""
        aliases = sorted({(r.get("name_verbatim") or "").strip() for r in members}
                         - {primary.strip(), ""})
        facilities.append({
            "facility_id": fid, "signature": sig, "n_rows": len(members),
            "n_sources": len({r["source_id"] for r in members}),
            "name": primary, "aliases": " | ".join(aliases),
            "state": first.get("state"), "city_norm": first.get("city_norm"),
            "street_key": first.get("street_key"), "tier": tier(members), "match_method": methods[sig],
            "no_fixed_plant": bool(first.get("no_fixed_plant")),
        })
    reg.save()
    return {"facilities": facilities, "rows": rows, "ids_issued": reg.issued_this_run,
            "n_facilities": len(facilities), "tiers": _count(f["tier"] for f in facilities)}


def _count(it):
    d: dict[str, int] = defaultdict(int)
    for x in it: d[x] += 1
    return dict(d)
