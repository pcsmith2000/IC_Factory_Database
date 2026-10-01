"""Score a demand pass against a reference set (docs/demand-research.md section 4).

The reference is a JSON file of projects found by careful research, each with evidence; it is never
committed (the repository is public) and never put in a prompt. Matching is per manufacturer: a pass
project matches a reference project when their distinctive name words overlap (any name or alias) or
their street addresses agree, and their states do not disagree.

    python -m pipeline.demand.score --pass pass/ --reference reference.json
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

from .pipeline import norm

GENERIC = {"the", "at", "of", "and", "on", "apartments", "apartment", "apts", "residences", "residence", "homes",
           "housing", "project", "hotel", "inn", "suites", "phase", "street", "st", "avenue", "ave", "road", "rd",
           "boulevard", "blvd", "by", "a", "in", "senior", "affordable", "community", "communities", "lofts",
           "flats", "studios", "modular", "building"}
COMPARED = ("city", "state", "units", "modules", "stories", "segment", "status")
REF_FIELD = {"city": "city", "state": "state", "units": "units", "modules": "modules", "stories": "stories",
             "segment": "segment", "status": "status"}


def maker_key(text: str, keys: list[str]) -> str | None:
    t = str(text or "").lower()
    aliases = {"vbc": ("volumetric", "vbc"), "autovol": ("autovol",), "guerdon": ("guerdon",),
               "zmodular": ("z modular", "zmodular", "z-modular")}
    for k in keys:
        if any(a in t for a in aliases.get(k, (k,))):
            return k
    return None


def tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", str(name or "").lower()) if t not in GENERIC and (len(t) >= 3 or t.isdigit())}


def street_key(addr: str) -> str:
    m = re.match(r"\s*(\d+)\s+(?:[NSEW]\.?\s+)?([A-Za-z0-9]+)", str(addr or ""))
    return f"{m.group(1)} {m.group(2).lower()}" if m else ""


def similarity(a_names: list[str], a_addr: list[str], b_names: list[str], b_addr: list[str]) -> float:
    best = 0.0
    for x in a_names:
        for y in b_names:
            tx, ty = tokens(x), tokens(y)
            if tx and ty:
                best = max(best, len(tx & ty) / min(len(tx), len(ty)))
            if norm(x) and norm(x) == norm(y):
                best = 1.0
    sa, sb = {street_key(a) for a in a_addr} - {""}, {street_key(b) for b in b_addr} - {""}
    if sa & sb:
        best = max(best, 1.0)
    return best


def pass_view(p: dict) -> dict:
    f = p["fields"]
    names = [p["name"]] + list(p.get("aliases") or [])
    addrs = [e["value"] for e in f.get("address") or []]
    names += addrs
    return {"names": names, "addrs": addrs, "state": p.get("state"),
            "values": {k: [e["value"] for e in f.get(k) or []] for k in COMPARED}}


def ref_view(r: dict) -> dict:
    names = [r["project_name"]] + list(r.get("alt_names") or [])
    addrs = [r["street_address"]] if r.get("street_address") else []
    return {"names": names + addrs, "addrs": addrs, "state": r.get("state"),
            "values": {k: r.get(REF_FIELD[k]) for k in COMPARED}}


def same(field: str, a, b) -> bool:
    if field in ("units", "modules", "stories"):
        try:
            return int(a) == int(b)
        except (TypeError, ValueError):
            return False
    return norm(a) == norm(b)


def score(doc: dict, reference: dict, threshold: float = 0.6) -> dict:
    projects = doc["projects"]
    keys = sorted({p["manufacturer"] for p in projects} | {k for k in ("vbc", "autovol", "guerdon", "zmodular")})
    refs = [dict(r, _key=maker_key(r.get("manufacturer"), keys)) for r in reference["projects"]]
    pv = [pass_view(p) for p in projects]
    rv = [ref_view(r) for r in refs]
    pairs = []
    for i, p in enumerate(projects):
        for j, r in enumerate(refs):
            if p["manufacturer"] != r["_key"]:
                continue
            if pv[i]["state"] and rv[j]["state"] and pv[i]["state"] != rv[j]["state"]:
                continue
            s = similarity(pv[i]["names"], pv[i]["addrs"], rv[j]["names"], rv[j]["addrs"])
            if s >= threshold:
                pairs.append((s, i, j))
    pairs.sort(key=lambda x: -x[0])
    p_to_r: dict[int, list[int]] = {}
    r_to_p: dict[int, list[int]] = {}
    for s, i, j in pairs:
        p_to_r.setdefault(i, []).append(j); r_to_p.setdefault(j, []).append(i)
    # One-to-one assignment for recall and field agreement, best similarity first.
    used_p, used_r, matches = set(), set(), []
    for s, i, j in pairs:
        if i not in used_p and j not in used_r:
            used_p.add(i); used_r.add(j); matches.append((i, j, s))
    agree = {f: {"compared": 0, "top_agrees": 0, "any_agrees": 0, "disagreements": []} for f in COMPARED}
    for i, j, _ in matches:
        for f in COMPARED:
            rv_val, pvals = rv[j]["values"][f], pv[i]["values"][f]
            if rv_val in (None, "") or not pvals:
                continue
            a = agree[f]; a["compared"] += 1
            if same(f, pvals[0], rv_val):
                a["top_agrees"] += 1
            if any(same(f, v, rv_val) for v in pvals):
                a["any_agrees"] += 1
            else:
                a["disagreements"].append({"project": projects[i]["name"], "pass": pvals, "reference": rv_val})
    per = {}
    for k in keys:
        rk = [j for j, r in enumerate(refs) if r["_key"] == k]
        pk = [i for i, p in enumerate(projects) if p["manufacturer"] == k]
        if not rk and not pk:
            continue
        found = [j for j in rk if j in used_r]
        per[k] = {"reference": len(rk), "pass": len(pk), "found": len(found),
                  "recall": round(len(found) / len(rk), 3) if rk else None,
                  "pass_in_reference": sum(1 for i in pk if i in p_to_r)}
    n_ref = len(refs)
    split = [{"reference": refs[j]["project_name"], "pass": [projects[i]["name"] for i in ps]}
             for j, ps in r_to_p.items() if len(ps) > 1]
    joined = [{"pass": projects[i]["name"], "reference": [refs[j]["project_name"] for j in rs]}
              for i, rs in p_to_r.items() if len(rs) > 1]
    novel = [{"manufacturer": p["manufacturer"], "name": p["name"], "state": p.get("state"),
              "country": ((p["fields"].get("country") or [{}])[0]).get("value"),
              "pages": p["n_pages"], "urls": list(dict.fromkeys(e["url"] for e in p["fields"]["name"]))[:3]}
             for i, p in enumerate(projects) if i not in p_to_r]
    missed = [{"manufacturer": refs[j]["_key"], "name": refs[j]["project_name"], "city": refs[j].get("city"),
               "state": refs[j].get("state")} for j in range(n_ref) if j not in used_r]
    cost = doc["summary"].get("cost") or {}
    return {
        "R1_recall": round(len(used_r) / n_ref, 3) if n_ref else None,
        "pass_projects": len(projects), "reference_projects": n_ref,
        "pass_in_reference": len(p_to_r), "novel_to_check": len(novel),
        "D1_split": len(split), "D2_joined": len(joined),
        "F1_agreement": {f: {"compared": a["compared"],
                             "top": round(a["top_agrees"] / a["compared"], 3) if a["compared"] else None,
                             "any": round(a["any_agrees"] / a["compared"], 3) if a["compared"] else None}
                         for f, a in agree.items()},
        "C1_list_usd": cost.get("list_usd"),
        "C2_list_usd_per_found": round(cost["list_usd"] / len(used_r), 4) if cost.get("list_usd") and used_r else None,
        "by_manufacturer": per,
        "lists": {"missed": missed, "novel": novel, "split": split, "joined": joined,
                  "disagreements": {f: a["disagreements"] for f, a in agree.items() if a["disagreements"]}},
    }


def markdown(sc: dict, summary: dict) -> str:
    L = [f"# Demand scorecard: {summary.get('config')} on {summary.get('batch')}", "",
         f"R1 recall **{sc['R1_recall']}** ({sc['reference_projects']} reference projects); "
         f"{sc['pass_projects']} pass projects, {sc['pass_in_reference']} in the reference, {sc['novel_to_check']} to check by hand; "
         f"D1 split {sc['D1_split']}, D2 joined {sc['D2_joined']}; list ${sc['C1_list_usd']}, "
         f"${sc['C2_list_usd_per_found']} per reference project found.", "",
         "| Manufacturer | Reference | Pass | Found | Recall | Pass in reference |", "|---|---|---|---|---|---|"]
    for k, v in sc["by_manufacturer"].items():
        L.append(f"| {k} | {v['reference']} | {v['pass']} | {v['found']} | {v['recall']} | {v['pass_in_reference']} |")
    L += ["", "| Field | Compared | Top value agrees | Any value agrees |", "|---|---|---|---|"]
    for f, a in sc["F1_agreement"].items():
        L.append(f"| {f} | {a['compared']} | {a['top']} | {a['any']} |")
    lists = sc["lists"]
    L += ["", "## Missed", ""] + [f"- {m['manufacturer']}: {m['name']} ({m['city']}, {m['state']})" for m in lists["missed"]]
    L += ["", "## Not in the reference (check by hand)", ""] + [
        f"- {n['manufacturer']}: {n['name']} ({n['state'] or n['country'] or '?'}) {' '.join(n['urls'])}" for n in lists["novel"]]
    if lists["split"]:
        L += ["", "## One reference project, several pass projects", ""] + [f"- {s['reference']}: {s['pass']}" for s in lists["split"]]
    if lists["joined"]:
        L += ["", "## One pass project, several reference projects", ""] + [f"- {s['pass']}: {s['reference']}" for s in lists["joined"]]
    for f, ds in lists["disagreements"].items():
        L += ["", f"## Disagreements: {f}", ""] + [f"- {d['project']}: pass {d['pass']} vs reference {d['reference']}" for d in ds]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pass", dest="pass_dir", required=True)
    ap.add_argument("--reference", required=True)
    a = ap.parse_args(argv)
    doc = json.loads((Path(a.pass_dir) / "projects.json").read_text())
    sc = score(doc, json.loads(Path(a.reference).read_text()))
    (Path(a.pass_dir) / "scorecard.json").write_text(json.dumps(sc, indent=1))
    (Path(a.pass_dir) / "scorecard.md").write_text(markdown(sc, doc["summary"]))
    print(json.dumps({k: v for k, v in sc.items() if k != "lists"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
