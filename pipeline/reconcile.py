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
    facilities = []
    for sig, members in clusters.items():
        fid = reg.get(sig)
        for r in members:
            r["facility_id"] = fid; r["match_method"] = methods[sig]
            r["match_confidence"] = {"entity+street": 0.95, "street_key": 0.90, "name+city": 0.70, "no-fixed-plant": 0.60}[methods[sig]]
        first = members[0]
        facilities.append({
            "facility_id": fid, "signature": sig, "n_rows": len(members),
            "n_sources": len({r["source_id"] for r in members}),
            "name": first.get("name_verbatim"), "state": first.get("state"), "city_norm": first.get("city_norm"),
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
