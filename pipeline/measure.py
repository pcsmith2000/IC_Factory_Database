"""Layer 7: measure and defend. Reported, not steered.

recall on the held-out control · per-state coverage vs CBP · bias index per state.
A state outside the band is published with its cause from registry/known-gaps.yaml; the run
never goes looking for a source to fix it.
"""
from __future__ import annotations
import csv
import re
from collections import defaultdict
from pathlib import Path
from .reconcile import norm_name


def _prefix_match(a: str, b: str) -> bool:
    """True when the shorter normalised name is a whole-word prefix of the longer one.

    The shorter side must itself be distinctive — two tokens, or eight characters. A one-word
    prefix matches far too much: "Blue Company" normalises to "blue" and prefix-matched "Blue
    Horse Building", and "CAVCO" prefix-matched "Cavco Industries R-Anell". Guarding the control
    name alone missed both, because in each the short side was the WAREHOUSE row.
    """
    if not a or not b or a == b:
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    if " " not in short and len(short) < 8:
        return False
    return long.startswith(short) and long[len(short)] == " "


def _key(name: str) -> str:
    """Normalised name with the spaces taken out, for the EXACT rungs only.

    Companies and the people listing them disagree about internal spacing, and the disagreement is
    not evidence of anything: the control list writes "Bankersteel" and "SR Sloan" where the
    sources write "Banker Steel" and "S R Sloan". Squashing is safe at this rung because the whole
    name still has to match — it is a spelling normalisation, not a loosening. The prefix rung
    keeps its spaces, because a whole-word prefix is exactly what it is testing.
    """
    return norm_name(name).replace(" ", "")


def _norm_city(city: str | None) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (city or "").lower()).split())


def load_frame(path: Path) -> dict[str, int]:
    """CSV: state,establishments — Census CBP state totals for the four core codes."""
    out = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            out[r["state"].upper()] = int(r["establishments"])
    return out


def recall(control_rows: list[dict], facilities: list[dict], crosswalk: dict[str, str],
           _detail: bool = False) -> dict:
    """Did the pipeline find the establishments a human already verified exist?

    The control list is PLANT-level, not company-level: "Builders FirstSource" is 21 rows in 13
    states, "The Truss Company" is 5. So matching is one-to-one — a database facility satisfies at
    most one control row — and the rungs are walked strongest-first across the whole list rather
    than row by row, so a row with a city takes the plant its city names before a bare name can
    claim it. Without that, 21 control rows matched the one Builders FirstSource plant in the
    warehouse and recall read 100% for a company we hold 1/21 of.

    Rungs: explicit crosswalk link · name+city+state · name+state · name alone. A row with no city
    starts at the rung its data supports. The name-alone rung is the point of the list, not a
    concession to it: 43 of the rows are a bare name, and requiring a state scored them 0% —
    indistinguishable from the pipeline having missed them.

    A row whose name is in the database but whose every candidate plant was already claimed by a
    stronger row is a miss, reported separately as `company_present_plant_missing`. That is the
    honest reading: the company is known, this establishment is not.

    Out-of-scope rows are excluded. A row with no `triage` value is treated as in scope and
    counted, so a bare name list works with no triage column at all — with the untriaged count
    reported, because "we assumed all 241 were in scope" is a claim the reader should see.
    """
    IN_SCOPE = {"in_scope_locatable", "in_scope_no_location"}
    in_scope, untriaged = [], 0
    for c in control_rows:
        t = (c.get("triage") or "").strip()
        if not t:
            untriaged += 1; in_scope.append(c)
        elif t in IN_SCOPE:
            in_scope.append(c)
    if not in_scope:
        # control/control-triaged.csv is empty, so there is nothing to have found. Reporting 0.0
        # states that the pipeline missed every establishment it was asked about, which is both
        # false and the most damning number in the release row — fact_release_metrics.recall has
        # carried it on every release to date. None says "not measured", the same distinction
        # GateResult.tested draws for G2 and G3.
        return {"in_scope": 0, "found": 0, "recall": None, "tested": False,
                "note": "no control rows triaged in_scope — recall cannot be measured, and 0.0 "
                        "would read as a total miss rather than an absent test",
                "by_method": {}}

    fac_ids = {f["facility_id"] for f in facilities}
    by_name_city: dict[tuple, list] = defaultdict(list)
    by_name_state: dict[tuple, list] = defaultdict(list)
    by_name: dict[str, list] = defaultdict(list)
    fac_names: list[tuple] = []
    for f in facilities:
        nm, st = _key(f["name"]), (f.get("state") or "").upper()
        # Layer 4 emits city_norm; dim_facility and hand-built fixtures carry city. Take whichever
        # is there — reading only "city" silently disabled this rung for every real run.
        by_name_city[(nm, _norm_city(f.get("city") or f.get("city_norm")), st)].append(f["facility_id"])
        by_name_state[(nm, st)].append(f["facility_id"])
        by_name[nm].append(f["facility_id"])
        # Spaced, deliberately: the prefix rung tests a whole-WORD prefix, which needs the words.
        fac_names.append((f["facility_id"], norm_name(f["name"]), st))

    def key_city(c):
        return (_key(c.get("name", "")), _norm_city(c.get("city")), (c.get("state") or "").upper())

    def key_state(c):
        return (_key(c.get("name", "")), (c.get("state") or "").upper())

    RUNGS = [("name+city", by_name_city, key_city, lambda c: bool((c.get("city") or "").strip())),
             ("name+state", by_name_state, key_state, lambda c: bool((c.get("state") or "").strip())),
             ("name", by_name, lambda c: _key(c.get("name", "")), lambda c: bool(_key(c.get("name", ""))))]

    taken: set[str] = set()
    matched: dict[int, str] = {}
    taken_by: dict[int, str] = {}
    by_method: dict[str, int] = defaultdict(int)
    for i, c in enumerate(in_scope):                       # crosswalk first, it is an assertion
        cw = crosswalk.get(c.get("control_id", ""))
        if cw and cw in fac_ids and cw not in taken:
            matched[i] = "crosswalk"; taken.add(cw); taken_by[i] = cw; by_method["crosswalk"] += 1
    fac_state = {f["facility_id"]: (f.get("state") or "").upper() for f in facilities}
    for label, index, keyfn, usable in RUNGS:              # then each rung across the whole list
        for i, c in enumerate(in_scope):
            if i in matched or not usable(c):
                continue
            cst = (c.get("state") or "").upper()
            # The name rung is state-blind so a bare-name list works at all, but blind is not the
            # same as ignoring a state the row DOES carry: two "Cavco Industries, TX" rows used to
            # take the Texas plant and then the Arizona one. A blank state on the warehouse row is
            # unknown, not disagreement — about a third of rows carry none.
            free = [fid for fid in index.get(keyfn(c), ())
                    if fid not in taken and not (cst and fac_state.get(fid) and cst != fac_state[fid])]
            if free:
                matched[i] = label; taken.add(free[0]); taken_by[i] = free[0]; by_method[label] += 1

    # Rung 5: the control list writes trading names ("Fading West"), the rosters write registered
    # ones ("FADING WEST BUILDING SYSTEMS, LLC"), and exact normalisation calls that a miss. So one
    # more pass where the shorter normalised name is a WORD-PREFIX of the longer — prefix, not
    # substring, because substring matched "American Truss Company" to "Barden Building Products /
    # North American Truss". States must agree, which is what rejects "84 Lumber" (VA) against
    # "84 Lumber Door Shop - Bessemer" (AL); a blank state on the warehouse row is treated as
    # unknown rather than as disagreement, because ~a third of rows carry no state at all and
    # holding that defect against the control row would understate recall for a second reason.
    # Reported under its own method name: it is weaker evidence than an exact name and the reader
    # should be able to subtract it.
    for i, c in enumerate(in_scope):
        if i in matched:
            continue
        cn, cst = norm_name(c.get("name", "")), (c.get("state") or "").upper()
        for fid, fn, fst in fac_names:
            if fid in taken or not _prefix_match(cn, fn):
                continue
            if cst and fst and cst != fst:
                continue
            matched[i] = "name-prefix"; taken.add(fid); taken_by[i] = fid; by_method["name-prefix"] += 1
            break

    misses, crowded = [], []
    for i, c in enumerate(in_scope):
        if i in matched:
            continue
        # "Company is present, this plant is not" must be judged the way the rungs match,
        # not on the exact key alone. 20 unmatched "Builders FirstSource" rows read as
        # plain misses while the warehouse held 90 BFS plants, because the exact key
        # "buildersfirstsource" does not appear in an index built from "Builders
        # FirstSource — Acworth GA Truss". They are plant-level gaps, and saying so is the
        # difference between "we have never heard of this company" and "we have this
        # company but not this site".
        cn = norm_name(c.get("name", ""))
        known = bool(by_name.get(_key(c.get("name", "")))) or any(
            _prefix_match(cn, fn) for _fid, fn, _st in fac_names)
        (crowded if known else misses).append(c.get("name", ""))
    if _detail:
        outcome = []
        for i, c in enumerate(in_scope):
            if i in matched:
                outcome.append((matched[i], taken_by.get(i), ""))
            else:
                cn = norm_name(c.get("name", ""))
                known = bool(by_name.get(_key(c.get("name", "")))) or any(
                    _prefix_match(cn, fn) for _fid, fn, _st in fac_names)
                outcome.append((None, None,
                                "company in database, THIS PLANT not" if known
                                else "not in any source we hold"))
    hits = len(matched)
    # A T0 row is a LEAD: a name the pipeline knows about with no location established. Counting
    # one as a found plant lets a source of bare names lift recall while the database gains nothing
    # anybody could visit — and a names-only roster is the cheapest source there is, so this is the
    # number most likely to be gamed by accident. Reported apart, always.
    tier = {f["facility_id"]: (f.get("tier") or "") for f in facilities}
    leads = sum(1 for i in matched if tier.get(taken_by.get(i), "") == "T0")
    out = {"in_scope": len(in_scope), "found": hits, "recall": hits / len(in_scope),
           "found_located": hits - leads, "found_lead_only": leads,
           "recall_located": (hits - leads) / len(in_scope),
           "tested": True, "by_method": dict(by_method), "untriaged_assumed_in_scope": untriaged,
           "company_present_plant_missing": len(crowded),
           "missed": sorted(misses)[:50], "n_missed": len(misses) + len(crowded)}
    # Recall on the sealed quarter, reported beside the headline. Everything that changes the
    # pipeline is diagnosed off `split: dev` rows; a prompt tuned until the rows it was shown come
    # back is not measuring anything. `sealed` is the number to quote, `recall` the number to work
    # against, and the gap between them is how much of the work was fitting.
    for name, want in (("dev", "dev"), ("sealed", "sealed")):
        idx = [i for i, c in enumerate(in_scope) if (c.get("split") or "dev").strip() == want]
        if idx:
            found = sum(1 for i in idx if i in matched)
            loc = sum(1 for i in idx if i in matched and tier.get(taken_by.get(i), "") != "T0")
            out[name] = {"in_scope": len(idx), "found": found, "recall": found / len(idx),
                         "found_located": loc, "recall_located": loc / len(idx)}
    if _detail:
        out["_detail"] = {"rows": in_scope, "outcome": outcome}
    return out


def coverage_and_bias(facilities: list[dict], frame: dict[str, int], band: tuple[float, float], min_share: float) -> dict:
    counted = [f for f in facilities if f["tier"] != "T0"]
    ours = defaultdict(int)
    for f in counted:
        ours[f["state"]] += 1
    n_ours, n_frame = len(counted), sum(frame.values())
    states = {}
    out_of_band = {}
    for st, est in frame.items():
        share_frame = est / n_frame
        share_ours = ours.get(st, 0) / n_ours if n_ours else 0.0
        bias = share_ours / share_frame if share_frame else 0.0
        states[st] = {"ours": ours.get(st, 0), "frame": est, "coverage": ours.get(st, 0) / est if est else 0.0, "bias": round(bias, 2)}
        if share_frame >= min_share and not (band[0] <= bias <= band[1]):
            out_of_band[st] = round(bias, 2)
    mab = sum(abs(s["bias"] - 1) for st, s in states.items() if frame[st] / n_frame >= min_share)
    n = sum(1 for st in states if frame[st] / n_frame >= min_share)
    return {"counted_facilities": n_ours, "frame_total": n_frame, "states": states,
            "out_of_band": out_of_band, "mean_abs_bias": round(mab / n, 3) if n else None}


def status_table(control_rows: list[dict], facilities: list[dict], crosswalk: dict[str, str]) -> list[dict]:
    """One row per control entry: did we find it, how, and if not, why not.

    Runs the SAME matcher as recall(), which is the point of it existing. Every earlier version of
    this table was rebuilt by hand outside the metric, and a hand-built copy of a matcher drifts
    from it — the first one reported nine plant-level gaps where the metric counted fourteen,
    because it tested "is this company known" on the exact key while the metric tested it the way
    the rungs match.

    `status` answers the control's question — is this establishment on our list — so a T0 lead is
    HAVE, with `has_address` saying which of those still needs a street.
    """
    r = recall(control_rows, facilities, crosswalk, _detail=True)
    detail = r.pop("_detail", {})
    by_id = {f["facility_id"]: f for f in facilities}
    out = []
    for c, (method, fid, why) in zip(detail["rows"], detail["outcome"]):
        f = by_id.get(fid) if fid else None
        on_list = f is not None
        out.append({
            "control_id": c.get("control_id", ""), "name": c.get("name", ""),
            "city": c.get("city", ""), "state": c.get("state", ""),
            "reason": c.get("reason", ""), "split": c.get("split", ""),
            "status": "HAVE" if on_list else "MISSING",
            "has_address": "" if not on_list else ("yes" if f.get("tier") != "T0" else "no — needs address lookup"),
            "matched_by": method or "", "matched_facility": (f or {}).get("name", ""),
            "matched_state": (f or {}).get("state", ""), "matched_tier": (f or {}).get("tier", ""),
            "why_missing": why or "",
        })
    return out
