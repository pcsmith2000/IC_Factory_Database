"""Stage 15 — what a facility MAKES, over ADL's taxonomy, from the merged record.

Layer 3 answers a different question: does this belong in the database at all. It judges one
source row from name, address, city, state and NAICS, its prompt is frozen, and its hash is part
of the release tag — so changing it invalidates the classify cache and moves every tag. This
stage asks what the plant makes, reads the MERGED facility, and touches none of that.

The merged record is the point. 871 source rows carry explicit product text — `product_types: CLT`
for Freres Lumber, `Glulam` for Arizona Structural Laminators, `certification_program: PFS TECO
client listing: modular` for Sturdisteel — and Layer 3's payload never included `notes`, so none
of it has ever reached a model. 20% of facilities merge more than one source (up to 12), so a
facility often holds several sources' product text at once where each row held one.

WHAT IS MEASURED. ADL's 218 labelled plants are the only ground truth. By GROUP they support a
real evaluation: Other 108, Modular 50, Panel 43, Mass Timber 10, Pods 6. By LEAF they mostly do
not — four leaves have no labelled example and seven have fewer than ten. So `capability_group`
carries a measured accuracy and `capability_leaf` is reported per class with its n. Averaging a
class of three into a headline would be a number that cannot fail.

ADL NEVER LOSES. Where ADL labelled a plant, survivorship ranks `primary_capability` above this
stage and the model cannot overwrite it. The 218 are ground truth, not competition — which also
keeps the eval honest: the stage is scored against labels it is forbidden to replace.
"""
from __future__ import annotations
import collections, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TAXONOMY = ROOT / "registry" / "taxonomy.yaml"
SOURCE_ID = "capability"
BASIS = "model_capability"
_NOISE = re.compile(r"[^a-z0-9 ]")


def norm(s: str) -> str:
    """Alias matching is case- and punctuation-insensitive on purpose: ADL's files write
    "Wood Structural Components (trusses etc)" where the taxonomy writes "(Trusses, etc.)", and
    both have to resolve to one leaf or the eval scores a typo as a disagreement."""
    return " ".join(_NOISE.sub(" ", (s or "").lower()).split())


class Taxonomy:
    def __init__(self, doc: dict):
        self.version = doc.get("version")
        self.groups = [g["name"] for g in doc["groups"]]
        self.leaves, self.group_of, self.by_alias = [], {}, {}
        self.signals, self.legacy, self.describe = {}, {}, {}
        for g in doc["groups"]:
            for code in g.get("legacy") or []:
                self.legacy[code] = g["name"]
            for leaf in g["leaves"]:
                nm = leaf["name"]
                self.leaves.append(nm)
                self.group_of[nm] = g["name"]
                self.signals[nm] = [norm(s) for s in (leaf.get("signals") or [])]
                # ADL's definition, shown to the model verbatim. "Open" vs "Closed" and "panel"
                # vs "module" are decided by a sentence, not by a leaf name.
                self.describe[nm] = " ".join((leaf.get("description") or "").split())
                for a in [nm] + list(leaf.get("aliases") or []):
                    self.by_alias[norm(a)] = nm
        self.unmapped_legacy = list(doc.get("unmapped_legacy") or [])

    def resolve(self, text: str, exact: bool = False) -> str | None:
        """A written capability -> one of our leaves, or None.

        Two callers, two standards. ADL's LABELS are prose written by people over several years —
        "Wood Structural Components (trusses etc)", "MgO Panel" — and a spelling difference there
        must never be scored as a disagreement, so a contained alias is enough. The MODEL'S ANSWER
        is held to `exact`: it was told to copy a leaf name from the prompt, and containment would
        quietly accept an invented one. "Steel Buildings Division" contains "steel buildings" and
        would otherwise land in Pre-Engineered Metal Building on the strength of a substring.
        """
        n = norm(text)
        if not n:
            return None
        if n in self.by_alias:
            return self.by_alias[n]
        if exact:
            return None
        # a contained alias, longest first, so "closed wood panel" beats "wood"
        for alias in sorted(self.by_alias, key=len, reverse=True):
            if len(alias) >= 6 and alias in n:
                return self.by_alias[alias]
        return None


def load(path: Path = TAXONOMY) -> Taxonomy:
    from ..registry import load_yaml
    return Taxonomy(load_yaml(path))


def evidence(fac: dict) -> str:
    """What the model is shown. Every part of it is something a source said, or Layer 3's own
    judgement — never a guess assembled here."""
    bits = []
    # SOURCE TEXT FIRST. Leading with naics primed the model: Rochester Homes carries "Missouri
    # PSC registered manufacturer, modular" and NAICS 321991, and the model answered HUD Modular
    # — the code, read first, beat the register that said otherwise in words.
    notes = (fac.get("notes") or "").strip()
    if notes:
        bits.append(notes[:700])
    for k, label in (("naics", "naics"), ("product_type", "layer3_type"),
                     ("website", "website"), ("sq_ft", "sq_ft")):
        v = (fac.get(k) or "").strip()
        if v:
            bits.append(f"{label}: {v}")
    return " ; ".join(bits)[:1100]


def signal_guess(tx: Taxonomy, fac: dict) -> tuple[str | None, str]:
    """A deterministic baseline, so the model has something to be better than.

    Not a fallback and not a rule engine: it exists to give the eval a floor. A stage whose only
    number is its own accuracy cannot tell "the model is good" from "the task is easy".
    """
    hay = norm(f"{fac.get('name','')} {evidence(fac)}")
    scored = []
    for leaf, sigs in tx.signals.items():
        found = [s for s in sigs if s and s in hay]
        if found:
            # Ties are broken by the LONGEST signal matched, then by leaf name — never by the
            # order of the YAML. Two hits on "wall panel"/"open panel" should not beat one hit on
            # "structural insulated panel" because a leaf happens to be listed first, and moving
            # a group in the file should not move the number this floor reports.
            scored.append((len(found), max(len(s) for s in found), leaf, found))
    if not scored:
        return None, "0 signal(s)"
    n, _, leaf, found = max(scored, key=lambda t: (t[0], t[1], t[2]))
    return leaf, f"{n} signal(s): {', '.join(sorted(found)[:4])}"


def assertions_for(facility_id: str, leaf: str, tx: Taxonomy, confidence: float,
                   reason: str, ev: str) -> list[dict]:
    from ._db import assertion
    group = tx.group_of[leaf]
    src = f"taxonomy v{tx.version} :: {reason[:120]} :: {ev[:400]}"
    return [assertion(facility_id, "capability_group", group, source_id=SOURCE_ID, basis=BASIS,
                      confidence=confidence, evidence=src),
            assertion(facility_id, "capability_leaf", leaf, source_id=SOURCE_ID, basis=BASIS,
                      confidence=confidence, evidence=src)]


# ---------------------------------------------------------------- evaluation
def evaluate(tx: Taxonomy, labelled: list[dict], predict) -> dict:
    """Score predictions against ADL's labels. Group and leaf separately, never averaged together.

    Per-class counts are reported with every rate. 218 labels over 18 leaves is 12 apiece, and a
    leaf with three examples has no meaningful accuracy — quoting one would be inventing a
    measurement, which is the thing this project's control test was retired for.
    """
    g_ok = g_n = l_ok = l_n = 0
    per_leaf = collections.defaultdict(lambda: [0, 0])
    per_group = collections.defaultdict(lambda: [0, 0])
    confusion = collections.Counter()
    unresolved = []
    for f in labelled:
        truth_leaf = tx.resolve(f.get("primary_capability", ""))
        if truth_leaf is None:
            unresolved.append(f.get("primary_capability"))
            continue
        truth_group = tx.group_of[truth_leaf]
        pred_leaf, _ = predict(f)
        pred_group = tx.group_of.get(pred_leaf) if pred_leaf else None
        g_n += 1
        per_group[truth_group][1] += 1
        if pred_group == truth_group:
            g_ok += 1
            per_group[truth_group][0] += 1
        l_n += 1
        per_leaf[truth_leaf][1] += 1
        if pred_leaf == truth_leaf:
            l_ok += 1
            per_leaf[truth_leaf][0] += 1
        elif pred_leaf:
            confusion[(truth_leaf, pred_leaf)] += 1
    return {
        "labelled": len(labelled), "scored": g_n,
        "unresolved_labels": sorted(set(x for x in unresolved if x)),
        "group_accuracy": round(g_ok / g_n, 3) if g_n else None,
        "leaf_accuracy_overall": round(l_ok / l_n, 3) if l_n else None,
        # every rate carries its n, so a class of three cannot be read as a measurement
        "per_group": {k: {"n": v[1], "correct": v[0],
                          "accuracy": round(v[0] / v[1], 3) if v[1] else None}
                      for k, v in sorted(per_group.items())},
        "per_leaf": {k: {"n": v[1], "correct": v[0],
                         "accuracy": round(v[0] / v[1], 3) if v[1] >= 10 else None,
                         "too_few_to_measure": v[1] < 10}
                     for k, v in sorted(per_leaf.items())},
        "top_confusions": [{"truth": t, "predicted": p, "n": n}
                           for (t, p), n in confusion.most_common(10)],
    }


# ---------------------------------------------------------------- evidence, from contract rows
# The product text this stage exists for is in the CONTRACT rows, not in golden. `notes` carries
# "product_types: CLT", "certification_program: PFS TECO client listing: modular" and the like for
# 19,000-odd rows, and golden has no notes column — the warehouse keeps a row's identity in
# ref_source_row and drops its notes. So the evidence is assembled from build/normalised/*.csv by
# the row_hash that every assertion already cites, which is also what keeps it attributable: the
# hash in the citation is the row a reader can go and read.
# A note segment that opens with a number, or that says what was skipped, is the source's own
# run accounting — "135 kept of 5454 cards across 13 state page(s)" — written once onto the first
# row of the source. It says nothing about the plant on that row, so it is dropped. Everything
# else is kept verbatim: "BFS branch type MF (manufacturing); site kind on page: Truss" and
# "Missouri PSC registered manufacturer, HUD-code manufactured homes" ARE the product evidence,
# and an earlier version of this filter threw both away looking for a `product_types:` key that
# these sources never write.
RUN_ACCOUNTING = re.compile(
    r"\bskipped\b|\bdropped as\b"                              # "2 non-US skipped"
    r"|\b\d+\s+(plants|rows|cards|records|members|manufacturers|organisations)\b"
    r"|\b\d+\s+kept\b|\bkept of\b",                          # "135 kept of 5454 cards"
    re.I)


def evidence_index(build: Path) -> dict[str, dict]:
    """facility_id -> the merged contract evidence for it.

    20% of facilities merge more than one source, so a facility often holds several sources'
    product text at once where each row held one. That is the whole reason this stage reads the
    merged record instead of re-asking Layer 3's question per row.
    """
    import csv
    by_hash: dict[str, dict] = {}
    for f in sorted((build / "normalised").glob("*.csv")):
        with f.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                h = (r.get("row_hash") or "").strip()
                if h:
                    by_hash[h] = r
    out: dict[str, dict] = {}
    seen: dict[str, set] = {}
    with (build / "assertions.csv").open(newline="", encoding="utf-8") as fh:
        for a in csv.DictReader(fh):
            fid, h = a.get("facility_id"), (a.get("row_hash") or "").strip()
            row = by_hash.get(h)
            if not fid or row is None or h in seen.setdefault(fid, set()):
                continue
            seen[fid].add(h)
            acc = out.setdefault(fid, {"notes": [], "website": "", "sq_ft": "",
                                       "naics": "", "sources": []})
            note = (row.get("notes") or "").strip()
            keep = [p.strip() for p in note.split("|")
                    if p.strip() and not RUN_ACCOUNTING.search(p.strip())]
            if keep:
                acc["notes"].append(f"{row.get('source_id','')}: " + " | ".join(keep))
            for k, col in (("website", "website"), ("sq_ft", "sq_ft"), ("naics", "naics_verbatim")):
                if not acc[k] and (row.get(col) or "").strip():
                    acc[k] = row[col].strip()
            if row.get("source_id"):
                acc["sources"].append(row["source_id"])
    for fid, acc in out.items():
        acc["notes"] = " | ".join(acc["notes"])[:900]
        acc["sources"] = ",".join(sorted(set(acc["sources"])))
    return out


# ---------------------------------------------------------------- the model
# Layer 3's prompt is frozen and its hash is in every release tag. This one is BUILT FROM THE
# TAXONOMY at call time and hashed separately, so adding a leaf changes this stage's prompt and
# nothing else — which is the whole reason the taxonomy is a file.
MODEL = "inception/mercury-2.5"
TEMPERATURE = 0.0
BATCH = 25
MAX_ATTEMPTS = 3


class BatchError(RuntimeError):
    """The batch came back unusable. The rows are re-asked; the run is not abandoned."""


def prompt_for(tx: Taxonomy) -> str:
    """The system prompt, assembled from the taxonomy. Every leaf appears with ADL's definition,
    because the hard calls in this taxonomy are definitional — Open vs Closed is about whether a
    face is enclosed, panel vs module is about whether it arrives three-dimensional — and a list
    of names alone makes the model guess at exactly those boundaries."""
    lines = ["You classify US industrialized-construction factories by WHAT THEY MAKE.",
             "",
             "The question is not whether the plant belongs in the database — it already does.",
             "The question is which single capability below best describes its output.",
             ""]
    for g in tx.groups:
        lines.append(f"## {g}")
        for leaf in tx.leaves:
            if tx.group_of[leaf] == g:
                lines.append(f"- **{leaf}** — {tx.describe[leaf]}")
        lines.append("")
    lines += [
        "RULES",
        "1. Answer with a leaf name copied EXACTLY as written above. Nothing else is a valid answer.",
        "2. Choose on the evidence given. The source a facility came from is evidence: a plant on "
        "SIPA's member list makes structural insulated panels, one on MBMA's roster makes "
        "pre-engineered metal buildings, one on a HUD-code state register makes HUD Modular.",
        "3. A trading word — Supply, Products, Industries, Building Systems — describes how a "
        "company sells, not what it makes. Never decide on one alone.",
        "4. Volumetric means the plant ships three-dimensional modules. A kit of frames and panels "
        "erected on site is NOT volumetric, however large the building.",
        "5. `layer3_type` is an earlier classifier's guess about ONE source row, made from its "
        "name and NAICS code alone. It is the weakest evidence in the record and never outranks "
        "a source that names the product.",
        "6. NAICS 321991 is the manufactured-homes code and is assigned by convention to modular "
        "plants that build nothing to the HUD standard. A register that says \"modular\" in words "
        "— \"Modular Manufacturers registry\", \"Modular Unit Manufacturer\", \"registered "
        "manufacturer, modular\" — outranks it. Answer HUD Modular only on POSITIVE evidence of "
        "the federal standard: a HUD-code register, a manufactured-housing plant list, or the "
        "words manufactured home, mobile home or HUD label.",
        "7. Volumetric or panel is decided by whether the plant ships a three-dimensional unit or "
        "a flat assembly, and a plant that does both is named by what it is registered to build. "
        "Wood or steel is decided by the FRAMING MATERIAL, which a roster rarely states: where "
        "nothing names the material, say so in the reason and take a low confidence rather than "
        "defaulting to wood.",
        "8. Exterior Envelope Panels is a facade or enclosure assembly — cladding, glazing, "
        "rainscreen, curtain wall, metal wall panels. It is not a SIP and not a framed wall "
        "panel, and the difference is whether the product is the WEATHER SKIN or the structure.",
        "9. Where the evidence genuinely does not say, answer with the leaf the NAICS code implies "
        "and give yourself a low confidence. Say so in the reason. A confident wrong answer costs "
        "more than an honest uncertain one.",
    ]
    return "\n".join(lines)


def prompt_hash(tx: Taxonomy) -> str:
    import hashlib
    return hashlib.sha256(prompt_for(tx).encode()).hexdigest()[:8]


def _call_once(tx: Taxonomy, facs: list[dict], model: str, temperature: float,
               repair: bool, usage_out: dict | None) -> dict[int, dict]:
    """One model call. Returns {index into facs: answer}, validated against the taxonomy.

    An answer outside the taxonomy is DROPPED, not mapped to a nearest leaf. A stage whose gate
    is "the value is a member of the taxonomy" cannot also quietly coerce values into it.
    """
    import json
    from .. import ai_client_and_model
    client, model_id, _ = ai_client_and_model(model)
    payload = [{"i": i, "name": f.get("name", ""), "evidence": evidence(f)}
               for i, f in enumerate(facs)]
    user = ("Classify each facility. Return a JSON array of {i, leaf, confidence, reason} with "
            "leaf copied exactly from the list, confidence 0-1, reason <= 15 words.\n"
            "Return strict JSON. Every key and every string value must be wrapped in double "
            "quotes. Do not use a double quote inside a value.\n\n" + json.dumps(payload))
    if repair:
        user = ("Your previous answer was not valid JSON. Return ONLY the array.\n\n" + user)
    # A FLOOR, not just a per-row budget. 80 tokens a row gave a 25-row batch a 3,000 ceiling;
    # mercury-2.5 spent 2,930 of it on all three attempts and returned truncated JSON every time,
    # so the run reported 0 of 25 answered and 8,788 tokens spent. A model that reasons before it
    # answers spends the budget thinking first — the same failure classify.py records for
    # nemotron-nano and gpt-5-nano. Output tokens bill for what is generated, so headroom given
    # to a terse model is free.
    want = min(32000, max(16000, 120 * len(facs) + 1000))
    msg = client.messages.create(model=model_id, max_tokens=want,
                                 extra_body={"temperature": temperature},
                                 system=prompt_for(tx),
                                 messages=[{"role": "user", "content": user}])
    if usage_out is not None and getattr(msg, "usage", None):
        usage_out["input_tokens"] = usage_out.get("input_tokens", 0) + msg.usage.input_tokens
        usage_out["output_tokens"] = usage_out.get("output_tokens", 0) + msg.usage.output_tokens
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    # A contract error that does not say what came back is unactionable. The first run of this
    # stage reported "3 contract errors" and nothing else, and the cause — a truncated array —
    # was only visible in the token counts. Carry the stop reason and a head of the text.
    stop = getattr(msg, "stop_reason", "?")
    head = text[:220].replace("\n", " ")
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise BatchError(f"no JSON array (stop_reason={stop}, {len(text)} chars): {head}")
    try:
        got = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise BatchError(f"invalid JSON (stop_reason={stop}, {len(text)} chars): {e} :: {head}")
    out = {}
    for o in got if isinstance(got, list) else []:
        try:
            i = int(o["i"])
        except (KeyError, TypeError, ValueError):
            continue
        leaf = tx.resolve(str(o.get("leaf", "")), exact=True)
        if leaf is None or not 0 <= i < len(facs):
            continue
        try:
            conf = max(0.0, min(1.0, float(o.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        out[i] = {"leaf": leaf, "confidence": conf,
                  "reason": str(o.get("reason", ""))[:160]}
    return out


def classify(tx: Taxonomy, facs: list[dict], model: str = MODEL,
             temperature: float = TEMPERATURE, usage_out: dict | None = None,
             stats: dict | None = None) -> dict[int, dict]:
    """One batch, classified. Rows the model garbles are re-asked; a run is not abandoned for them.

    Retries raise the temperature, because asking the same question at temperature 0 returns the
    same broken answer — an identical retry is not a retry. Rows still unanswered after the last
    attempt are RETURNED MISSING rather than filled from signal_guess: the floor is a yardstick
    for the model, and silently substituting it would make the model's score include it.
    """
    answers: dict[int, dict] = {}
    pending = list(range(len(facs)))
    for attempt in range(MAX_ATTEMPTS):
        if stats is not None and attempt:
            stats["reasks"] = stats.get("reasks", 0) + 1
        try:
            got = _call_once(tx, [facs[i] for i in pending], model,
                             temperature + 0.2 * attempt, bool(attempt), usage_out)
        except BatchError as e:
            if stats is not None:
                stats["contract_errors"] = stats.get("contract_errors", 0) + 1
                stats.setdefault("contract_error_detail", []).append(str(e)[:300])
            continue
        for local, o in got.items():
            answers[pending[local]] = o
        pending = [i for i in range(len(facs)) if i not in answers]
        if not pending:
            break
    if pending and stats is not None:
        stats["unanswered"] = stats.get("unanswered", 0) + len(pending)
    return answers


# ---------------------------------------------------------------- eval entry point
def labelled_from(build: Path, limit: int = 0) -> list[dict]:
    """ADL's labelled facilities, with the contract evidence joined on.

    Everything comes out of one build directory — golden.csv for the labels, normalised/*.csv and
    assertions.csv for the evidence — so an eval is reproducible from a single artifact and does
    not depend on the warehouse being reachable or unchanged.
    """
    import csv
    idx = evidence_index(build)
    out = []
    with (build / "golden.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if not (r.get("primary_capability") or "").strip():
                continue
            ev = idx.get(r.get("facility_id", ""), {})
            out.append({**r, **{k: v for k, v in ev.items() if k in ("notes", "website", "sq_ft")}})
    out.sort(key=lambda r: r["facility_id"])            # deterministic before any limit
    if not limit or limit >= len(out):
        return out
    # EVENLY SPACED, not the first N. facility_ids cluster by the source that issued them, so the
    # first 25 are not a sample of the 218 — their floor scored 0.44 against 0.372 for the whole
    # set, which would have read as the model being tested on a harder or easier problem than the
    # one it will do. Taking every kth row keeps a cheap run comparable to a full one.
    step = len(out) / limit
    return [out[int(i * step)] for i in range(limit)]


def unlabelled_from(build: Path, limit: int = 0) -> list[dict]:
    """The facilities this stage EXISTS for — the ones ADL never labelled.

    Same evidence join as the labelled set, so what the eval measured is what production sees.
    Sampled every kth row rather than taking a prefix, for the same reason: facility_ids cluster
    by the source that issued them.
    """
    import csv
    idx = evidence_index(build)
    out = []
    with (build / "golden.csv").open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if (r.get("primary_capability") or "").strip():
                continue                      # ADL already said; this stage must not overwrite it
            ev = idx.get(r.get("facility_id", ""), {})
            out.append({**r, **{k: v for k, v in ev.items() if k in ("notes", "website", "sq_ft")}})
    out.sort(key=lambda r: r["facility_id"])
    if not limit or limit >= len(out):
        return out
    step = len(out) / limit
    return [out[int(i * step)] for i in range(limit)]


def _predict_report(tx: Taxonomy, rows: list[dict], answers: dict[str, dict]) -> dict:
    """What a production pass produced, with no accuracy claimed anywhere.

    These facilities have no label, so there is NOTHING to score against and the report says so
    rather than reaching for a number. What it can show is the shape of the answer: the leaf
    distribution, how confident the model was, and how often it fell back on NAICS — which is the
    honest way to tell "it classified them" from "it classified them well".
    """
    leaves = collections.Counter(o["leaf"] for o in answers.values())
    groups = collections.Counter(tx.group_of[o["leaf"]] for o in answers.values())
    conf = sorted(o["confidence"] for o in answers.values())
    low = [o for o in answers.values() if o["confidence"] < 0.5]
    return {
        "asked": len(rows), "answered": len(answers),
        "no_evidence_beyond_a_name": sum(1 for r in rows if not evidence(r).strip()),
        "by_group": dict(groups.most_common()),
        "by_leaf": dict(leaves.most_common()),
        "confidence": {"median": conf[len(conf) // 2] if conf else None,
                       "under_0.5": len(low),
                       "min": conf[0] if conf else None, "max": conf[-1] if conf else None},
        # A leaf nothing lands in is as much a finding as one everything lands in.
        "leaves_never_used": sorted(set(tx.leaves) - set(leaves)),
        "examples": [{"name": r.get("name", ""), "leaf": answers[r["facility_id"]]["leaf"],
                      "confidence": answers[r["facility_id"]]["confidence"],
                      "reason": answers[r["facility_id"]]["reason"]}
                     for r in rows[:: max(1, len(rows) // 12)][:12]
                     if r["facility_id"] in answers],
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse, json, time
    ap = argparse.ArgumentParser(description="stage 15 — capability classification and its eval")
    ap.add_argument("--build", default="build", type=Path)
    ap.add_argument("--limit", type=int, default=0, help="first N labelled facilities, 0 = all")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--floor-only", action="store_true", help="no model call, no spend")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--predict", type=int, default=0,
                    help="classify N UNLABELLED facilities instead of scoring the labelled set")
    a = ap.parse_args(argv)

    tx = load()
    if a.predict:
        rows = unlabelled_from(a.build, a.predict)
        answers: dict[str, dict] = {}
        usage, stats = {}, {}
        t0 = time.time()
        for i in range(0, len(rows), a.batch):
            chunk = rows[i:i + a.batch]
            for local, o in classify(tx, chunk, a.model, usage_out=usage, stats=stats).items():
                answers[chunk[local]["facility_id"]] = o
        report = {"taxonomy_version": tx.version, "prompt_hash": prompt_hash(tx),
                  "model": a.model, "seconds": round(time.time() - t0, 1),
                  "usage": usage, **stats, "predict": _predict_report(tx, rows, answers)}
        text = json.dumps(report, indent=2)
        print(text)
        pr = report["predict"]
        print(f"HEADLINE predict answered={pr['answered']}/{pr['asked']} "
              f"groups={pr['by_group']} median_confidence={pr['confidence']['median']} "
              f"tokens={usage.get('input_tokens', 0)}in/{usage.get('output_tokens', 0)}out")
        if a.out:
            a.out.write_text(text)
        return 0

    rows = labelled_from(a.build, a.limit)
    report = {"taxonomy_version": tx.version, "leaves": len(tx.leaves),
              "prompt_hash": prompt_hash(tx), "model": a.model,
              "labelled": len(rows),
              "with_product_text": sum(1 for r in rows if (r.get("notes") or "").strip()),
              "floor": evaluate(tx, rows, lambda f: signal_guess(tx, f))}
    if not a.floor_only:
        answers: dict[str, dict] = {}
        usage: dict = {}
        stats: dict = {}
        t0 = time.time()
        for i in range(0, len(rows), a.batch):
            chunk = rows[i:i + a.batch]
            for local, o in classify(tx, chunk, a.model, usage_out=usage, stats=stats).items():
                answers[chunk[local]["facility_id"]] = o
        report["model_run"] = {
            "answered": len(answers), "asked": len(rows), "seconds": round(time.time() - t0, 1),
            "usage": usage, **stats,
            # Unanswered rows are scored as wrong, not skipped. A model that answers half the
            # batch confidently is not better than one that answers all of it.
            "result": evaluate(tx, rows, lambda f: (
                (answers.get(f["facility_id"], {}).get("leaf"),
                 answers.get(f["facility_id"], {}).get("reason", "")))),
        }
    text = json.dumps(report, indent=2)
    print(text)
    # A one-line headline last, so a run's result can be read off the end of a log without
    # paging back through 19 leaves of JSON.
    f, m = report["floor"], report.get("model_run")
    print(f"HEADLINE floor group={f['group_accuracy']} leaf={f['leaf_accuracy_overall']}"
          + (f" | {a.model} group={m['result']['group_accuracy']} "
             f"leaf={m['result']['leaf_accuracy_overall']} "
             f"answered={m['answered']}/{m['asked']} "
             f"tokens={m['usage'].get('input_tokens', 0)}in/{m['usage'].get('output_tokens', 0)}out"
             if m else ""))
    if a.out:
        a.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
