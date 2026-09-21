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

    def resolve(self, text: str) -> str | None:
        """A written capability -> one of our leaves, or None. Used for ADL's labels and for the
        model's answer, so a spelling difference never becomes a disagreement."""
        n = norm(text)
        if not n:
            return None
        if n in self.by_alias:
            return self.by_alias[n]
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
    for k, label in (("naics", "naics"), ("product_type", "layer3_type"),
                     ("website", "website"), ("sq_ft", "sq_ft")):
        v = (fac.get(k) or "").strip()
        if v:
            bits.append(f"{label}: {v}")
    notes = (fac.get("notes") or "").strip()
    if notes:
        # the product and certification text, which is the part that actually names a product
        keep = [p.strip() for p in notes.split("|")
                if re.match(r"\s*(product_types|certification_program|certification_categories"
                            r"|evidence_text|source)\s*:", p)]
        if keep:
            bits.append(" | ".join(keep)[:600])
    return " ; ".join(bits)[:900]


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
