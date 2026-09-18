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
    """T0 means no PLACEABLE address, which is not the same as no address given.

    Worth stating because the gap looks like a bug and is not. Of run 22's 1,382 T0 leads, 287 have
    a member row whose `address_verbatim` is non-empty — 293 rows in all, mostly EPA FRS (181),
    in_dhs (40) and mbi_members (36). Every one of them is unplaceable rather than unparsed:

        MAIN ST · HWY 278 W · SARDIS RD. · 10TH AVENUE SOUTH · SHANHOUSE BOULEVARD
        BEDFORD INDL. PARK · HIGHWAY 41 N & CAVALIER ROAD · AT OR NEAR RIVERFRONT DR.
        P.O. BOX 310 · P O BOX 6868

    street_key needs a house number and refuses a PO box, both correctly: a street name with no
    number does not identify a building, and a mailbox is not a plant. Reaching these needs a
    geocoder or the enrichment pass, not a parser fix — and street_key must not be loosened to
    chase them, because it feeds the facility signature and every id would be re-issued.
    """
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

    # Pass 1b: the lead that HAS a city and state but a different NAME for the same plant. Pass 1
    # demands the normalised names be equal, and these are not: pa_dced writes "VBC Manufacturing"
    # in Berwick PA where iibc writes "VBC BERWICK, LLC" at 159 Power House Rd, and "Cavco
    # Manufacturing LLC" in Emlenton PA where iibc writes "CAVCO-EMLENTON" at 4 Pennwest Way. Two
    # ids per plant, and the addressless one is a T0 lead standing beside a located facility.
    #
    # The name test is weakened to a shared FIRST token, and the place carries the weight instead:
    # city and state must both match exactly and exactly one addressed cluster may qualify. That
    # trade is only safe because both sides are our own rows — the same rule was measured as a
    # control-matching rung and was 29% wrong, where the failures were a shared city name (York),
    # a state name (Arizona) or a common word (American). Within one city, with one candidate, a
    # shared first token plus an identical place is a different proposition, and G2 is watching.
    # A COMMON head token alone is not evidence, and measuring said so: of 76 merges this pass made
    # on run 22, 21 turn on a head appearing in more than 1% of facilities — but 18 of those also
    # share a second token ("CHAMPION HOME BUILDERS" with "CHAMPION HOME BUILDERS #2"), which is
    # plainly the same plant. Only 3 rest on the common word alone, and one is wrong: "Modular
    # Technology" and "Modular Solutions, Ltd" are different firms in Phoenix that share nothing
    # but "modular". So: either the head is distinctive, or there must be more than one shared
    # token. The threshold is read off this database's own token frequency, not off known answers.
    df: dict[str, int] = defaultdict(int)
    for members in clusters.values():
        for tok in set(norm_name(members[0].get("name_verbatim", "")).split()):
            df[tok] += 1
    common = max(5, len(clusters) // 100)     # below five clusters a frequency means nothing

    addressed_place: dict[tuple, list[str]] = defaultdict(list)
    for sig, members in clusters.items():
        if methods.get(sig) in ("street_key", "entity+street"):
            f = members[0]
            head = norm_name(f.get("name_verbatim", "")).split()
            if head and f.get("state") and f.get("city_norm"):
                addressed_place[(f["state"], f["city_norm"], head[0])].append(sig)
    for sig in [s for s, m in methods.items() if m == "name+city"]:
        f = clusters[sig][0]
        head = norm_name(f.get("name_verbatim", "")).split()
        if not head or len(head[0]) < 3 or not f.get("state") or not f.get("city_norm"):
            continue
        targets = addressed_place.get((f["state"], f["city_norm"], head[0]), [])
        if len(targets) != 1:
            continue
        if df[head[0]] > common:
            shared = set(head) & set(norm_name(clusters[targets[0]][0].get("name_verbatim", "")).split())
            if len(shared) < 2:
                continue        # one common word in one town is a coincidence, not a plant
        for r in clusters[sig]:
            r["attached_from"] = "name-head+city"
        clusters[targets[0]].extend(clusters.pop(sig))
        methods.pop(sig, None)
        folded += 1

    # Second pass: the lead that carries no city OR state either. The pass above needs all three
    # to agree and so cannot see these at all — they key on ('', '', name) and `all(key)` drops
    # them. They are a real duplicate, not a separate plant: run 22 published "Falcon Structures"
    # as a T0 lead beside IC-93515 FALCON STRUCTURES in Manor TX, and "Neopod Systems LLC" beside
    # IC-94144 in New Braunfels, and the control list counted both as plants we hold only as a
    # name. 6 of its 17 lead-only matches are this shape.
    #
    # The name alone is doing all the work here, so it must be distinctive and unique NATIONALLY:
    # exactly one addressed cluster anywhere carries that normalised name, and the name is two
    # tokens or eight characters — the same distinctiveness bar `_prefix_match` applies, and for
    # the same reason, because norm_name reduces "The Truss Company" to "truss".
    national: dict[str, list[str]] = defaultdict(list)
    for sig, members in clusters.items():
        if methods.get(sig) in ("street_key", "entity+street"):
            n = norm_name(members[0].get("name_verbatim", ""))
            if n:
                national[n].append(sig)
    for sig in [s for s, m in methods.items() if m == "name+city"]:
        f = clusters[sig][0]
        if f.get("state") or f.get("city_norm"):
            continue                      # the pass above already had its chance at these
        n = norm_name(f.get("name_verbatim", ""))
        if not n or (" " not in n and len(n) < 8):
            continue                      # one generic token would fold half the industry
        targets = national.get(n, [])
        if len(targets) != 1:
            continue                      # two plants of that name: we cannot tell which
        for r in clusters[sig]:
            r["attached_from"] = "name-only"
        clusters[targets[0]].extend(clusters.pop(sig))
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
