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


def _light_name(name: str) -> str:
    """Punctuation stripped, corporate words KEPT — the form the prefix rung compares.

    norm_name removes "the", "company", "inc", "corp", which is right for the exact rungs and
    pathological for the prefix one: "The Truss Company" becomes "truss", a single generic token,
    and the prefix rung refuses a one-word key on purpose because "truss" would prefix half the
    industry. All five of that company's control rows were unmatchable while all eight of its
    plants sat in the warehouse with street addresses, carried as "The Truss Company - Eugene".

    Keeping the corporate words makes both sides comparable — "the truss company" is a whole-word
    prefix of "the truss company eugene".

    It is an ADDITIONAL form, not a replacement. Swapping the prefix rung over to it wholesale was
    measured against run 35255141179 and cost two matches net, 49 prefix hits down to 47: the
    stripped form wins on names where a corporate word is the only difference. So the rung tries
    the stripped form first and this one second, and a row matches if either does.

    Deliberately local to Layer 7. reconcile.norm_name feeds the facility signature, and changing
    it would re-key every addressless cluster and re-issue their IDs; G3 exists to catch that.
    Matching is measurement, and measurement must not move identity.
    """
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split())


def _company_known(c: dict, by_name: dict, fac_names: list) -> bool:
    """Is this control row's COMPANY anywhere in the warehouse, judged the way the rungs match?

    One helper rather than two copies: the count and the per-row explanation disagreed once
    already, reporting nine plant-level gaps where the metric counted fourteen.
    """
    cn, cl = norm_name(c.get("name", "")), _light_name(c.get("name", ""))
    if by_name.get(_key(c.get("name", ""))):
        return True
    return any(_prefix_match(cn, fn) or _prefix_match(cl, fl) for _fid, fn, fl, _st in fac_names)


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


def ingested_index(normalised_dir: Path) -> dict:
    """Every establishment name ANY source ingested, before a classifier saw it.

    recall() can only see published facilities, so the only answer it can give for an unmatched
    control row is "not in the published warehouse". For 151 missing rows in run 22 that answer was
    being WRITTEN OUT as "not in any source we hold", which is a different and much stronger claim,
    and nothing had checked it. Measured against the 103,017 normalised rows it is also wrong for
    29 of them: those plants were ingested and then lost — 9 were even labelled IC — while 122
    genuinely appear in no source. The distinction decides where work goes, because no prompt edit
    reaches a row nothing ever fetched.

    Indexed by FIRST TOKEN, which is exact rather than a heuristic: `_prefix_match` only succeeds
    when one normalised name is a whole-word prefix of the other, so the two necessarily share a
    first token. That turns a 151 x 103,017 scan into a dict lookup. The stripped and light forms
    are kept in separate indexes so a stripped name is never compared against a light one, which
    is the same pairing `_company_known` uses.
    """
    exact: dict[str, set] = defaultdict(set)
    first_norm: dict[str, list] = defaultdict(list)
    first_light: dict[str, list] = defaultdict(list)
    for path in sorted(Path(normalised_dir).glob("*.csv")):
        src = path.stem
        with open(path, newline="", encoding="utf-8", errors="replace") as fh:
            for r in csv.DictReader(fh):
                nm = r.get("name_verbatim") or ""
                if not nm:
                    continue
                exact[_key(nm)].add(src)
                for form, idx in ((norm_name(nm), first_norm), (_light_name(nm), first_light)):
                    if form:
                        idx[form.split()[0]].append((form, src))
    return {"exact": exact, "first_norm": first_norm, "first_light": first_light}


def sources_holding(name: str, index: dict) -> set:
    """Which sources ingested a row under this name, judged the way the rungs match."""
    if not index:
        return set()
    got = set(index["exact"].get(_key(name), ()))
    cn, cl = norm_name(name), _light_name(name)
    for form, idx in ((cn, index["first_norm"]), (cl, index["first_light"])):
        if not form:
            continue
        for candidate, src in idx.get(form.split()[0], ()):
            if _prefix_match(form, candidate):
                got.add(src)
    return got


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
        fac_names.append((f["facility_id"], norm_name(f["name"]), _light_name(f["name"]), st))

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
        cn, cl = norm_name(c.get("name", "")), _light_name(c.get("name", ""))
        cst = (c.get("state") or "").upper()
        for fid, fn, fl, fst in fac_names:
            if fid in taken or not (_prefix_match(cn, fn) or _prefix_match(cl, fl)):
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
        known = _company_known(c, by_name, fac_names)
        (crowded if known else misses).append(c.get("name", ""))
    if _detail:
        outcome = []
        for i, c in enumerate(in_scope):
            if i in matched:
                outcome.append((matched[i], taken_by.get(i), ""))
            else:
                known = _company_known(c, by_name, fac_names)
                outcome.append((None, None,
                                "company in database, THIS PLANT not" if known
                                else "not in the published warehouse"))
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


def status_table(control_rows: list[dict], facilities: list[dict], crosswalk: dict[str, str],
                 ingested: dict | None = None) -> list[dict]:
    """One row per control entry: did we find it, how, and if not, why not.

    Runs the SAME matcher as recall(), which is the point of it existing. Every earlier version of
    this table was rebuilt by hand outside the metric, and a hand-built copy of a matcher drifts
    from it — the first one reported nine plant-level gaps where the metric counted fourteen,
    because it tested "is this company known" on the exact key while the metric tested it the way
    the rungs match.

    `status` answers the control's question — is this establishment on our list — so a T0 lead is
    HAVE, with `has_address` saying which of those still needs a street.

    `ingested` is the index from `ingested_index()`. With it, a MISSING row says whether any source
    fetched the name at all and names the sources that did, which separates "no source has this
    plant" from "a source had it and the pipeline lost it". Without it the row falls back to what
    recall() alone can see, and says only that the plant is not in the published warehouse — it
    does NOT claim no source holds it, because nothing checked.
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
            "why_missing": why or "", "ingested_by": "",
        })
        if on_list or ingested is None:
            continue
        # A plant no source ever fetched is beyond any amount of prompt or matcher work; one that
        # WAS fetched and did not survive is a leak with a fixable cause. Only the second kind is
        # worth an edit, and until this column existed both read identically.
        srcs = sources_holding(c.get("name", ""), ingested)
        out[-1]["ingested_by"] = " ".join(sorted(srcs))
        out[-1]["why_missing"] = ("never ingested — no source holds this name" if not srcs
                                  else f"ingested but lost before publication ({out[-1]['why_missing']})")
    return out


def crosswalk_candidates(control_rows: list[dict], facilities: list[dict],
                         crosswalk: dict[str, str]) -> list[dict]:
    """Unmatched control rows sharing a first name-token with a facility in the SAME city and state.

    A REVIEW QUEUE, deliberately not a rung. The control writes "Company - Site" and the source
    writes the site its own way, so no prefix of one is a prefix of the other: "Cavco - Penn West"
    in Emlenton PA is "CAVCO-EMLENTON", "Mercer Mass Timber - WA" in Spokane is "Mercer", "VBC" in
    Berwick PA is "VBC BERWICK, LLC". Seventeen such pairs exist against run 22, and about twelve
    of them are the same plant.

    It was built as a sixth rung first, and measuring it is why it is not one. Scoring the seventeen
    by hand: 12 right, 5 wrong — Sterling Structural/Sterling Solutions, Clark Pacific/ClarkDietrich,
    York P-B Truss/York International, Arizona Building Supply/Fleetwood Homes of Arizona, American
    Builders Supply/A American Container. Roughly a 29% false-positive rate, which recall cannot
    carry.

    The obvious repair — demand a RARE first token — does not work, and the numbers say so plainly.
    Document frequency across the 4,204 facility names, right answers marked:

        mmy 1 RIGHT · trussco 1 RIGHT · vbc 2 RIGHT · vantem 2 RIGHT · mercer 2 RIGHT · nvr 2 RIGHT
        sterling 2 WRONG · clark 3 WRONG · york 3 WRONG · arizona 5 WRONG
        premier 8 RIGHT · ritz 9 RIGHT · superior 19 RIGHT · cavco 32 RIGHT · american 56 WRONG

    Rarity does not separate them: "sterling" is as rare as "mercer" and wrong, "cavco" is sixteen
    times commoner and right. Any threshold that sorted this list would be one I had chosen by
    reading the answers, which is how a metric starts scoring itself.

    What separates them is whether the shared token is the company's name or the town's, and no
    string test settles that. A human does, once, and the answer becomes an assertion in the
    crosswalk — where the crosswalk rung already counts it as found, because an assertion is
    evidence and a coincidence of spelling is not.
    """
    r = recall(control_rows, facilities, crosswalk, _detail=True)
    detail = r.pop("_detail", {})
    rows = detail.get("rows", [])
    matched_ids = {fid for _m, fid, _w in detail.get("outcome", []) if fid}
    df: dict[str, int] = defaultdict(int)
    for f in facilities:
        for tok in set(norm_name(f["name"]).split()):
            df[tok] += 1
    out = []
    for c, (_method, fid, _why) in zip(rows, detail.get("outcome", [])):
        if fid:
            continue
        head = norm_name(c.get("name", "")).split()
        ccity, cst = _norm_city(c.get("city")), (c.get("state") or "").upper()
        if not head or len(head[0]) < 3 or not ccity or not cst:
            continue
        ctok = set(head)
        for f in facilities:
            if f["facility_id"] in matched_ids:
                continue
            ftok = set(norm_name(f["name"]).split())
            if head[0] not in ftok:
                continue
            if _norm_city(f.get("city") or f.get("city_norm")) != ccity:
                continue
            if (f.get("state") or "").upper() != cst:
                continue
            out.append({
                "control_id": c.get("control_id", ""), "control_name": c.get("name", ""),
                "city": c.get("city", ""), "state": cst,
                "facility_id": f["facility_id"], "facility_name": f.get("name", ""),
                "facility_tier": f.get("tier", ""), "head_token": head[0],
                "head_token_facilities": df[head[0]],
                "shared_tokens": " ".join(sorted(ctok & ftok)),
                "verdict": "",
            })
            break
    return out


def source_gap(status: list[dict]) -> dict:
    """How much of the shortfall any pipeline work could reach, and how much needs a new source.

    `ceiling` is the recall this database would reach if every row that was ingested and lost were
    recovered and nothing else changed — the honest upper bound on classifier, matcher and
    resolution work against today's sources. Run 22: 90 found, 29 recoverable, 122 never ingested,
    so the ceiling is 49.4% and 80% is unreachable without fetching 74 more plants.
    """
    have = sum(1 for r in status if r["status"] == "HAVE")
    missing = [r for r in status if r["status"] == "MISSING"]
    never = sum(1 for r in missing if r["why_missing"].startswith("never ingested"))
    lost = len(missing) - never
    n = len(status)
    return {"in_scope": n, "found": have, "ingested_but_lost": lost, "never_ingested": never,
            "ceiling": round((have + lost) / n, 4) if n else None}
