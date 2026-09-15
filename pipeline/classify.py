"""Layer 3: candidate generation (deterministic) + classification (AI, regimented).

Only sources with `needs_classify: true` pass through here. The classifier is the frozen prompt
in prompts/CLASSIFIER-PROMPT.md; the model id, temperature and prompt hash are recorded in the
run record. Batches are keyed by content hash so a re-run is a no-op for unchanged rows.
Seeded positives/negatives are hidden in every batch and scored by gate G5.

Today the classify() call is executed through the Cowork harness; the target is the Messages
API. Both go through the same `classify_batch` signature so swapping is one function.
"""
from __future__ import annotations
import hashlib, json, os, re
from pathlib import Path

LABELS = {"IC", "NOT-IC", "UNCERTAIN"}

# keyword × NAICS-family matrix. The pairing carries the signal — neither alone.
# COMPONENT is near-zero precision unless paired with 3212xx/3219xx. OFFSITE is permit language: excluded.
KEYWORD_NAICS = {
    r"\bmodular\b":            {"3219", "3212", "3323", "2362", "2381", "4233"},
    r"\bprefab":               {"3219", "3212", "3323", "2362", "2381", "4233"},
    r"\bmanufactured hom":     {"3219"},
    r"\bpanel(s|ized|ised)?\b":{"3219", "3212", "3323"},
    r"\btruss":                {"3212", "3219"},
    r"\bprecast\b":            {"3273", "3272"},
    r"\bmass timber|\bglulam|\bclt\b|\bcross.laminated": {"3212", "3211"},
    r"\bsip(s)?\b|\bstructural insulated": {"3219", "3212"},
    r"\bmetal building|\bpre.?engineered": {"3323"},
    r"\bcomponent":            {"3212", "3219"},
    r"\bvolumetric|\bpod(s)?\b": {"3219", "3323"},
}
PLACENAME_COLLISIONS = {"trussville", "old forge", "campanello"}


def candidates(rows: list[dict], core_naics: set[str]) -> list[dict]:
    """Return rows that are in a core NAICS code, or match keyword × NAICS family."""
    out = []
    for r in rows:
        naics = (r.get("naics_verbatim") or "").strip()
        name = (r.get("name_verbatim") or "").lower()
        if naics in core_naics:
            r["_candidate_reason"] = f"core naics {naics}"; out.append(r); continue
        if any(p in name for p in PLACENAME_COLLISIONS):
            continue
        for pat, fams in KEYWORD_NAICS.items():
            if re.search(pat, name) and naics[:4] in fams:
                r["_candidate_reason"] = f"{pat} × {naics[:4]}"; out.append(r); break
    return out


def prompt_hash(prompt_path: Path) -> str:
    return hashlib.sha256(prompt_path.read_bytes()).hexdigest()[:12]


def batch_key(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(r["row_hash"].encode())
    return h.hexdigest()[:16]


def classify_batch(rows: list[dict], prompt: str, model: str, temperature: float) -> list[dict]:
    """One regimented model call. Returns [{row_hash, label, confidence, type, reason}] in row order.

    Target implementation (Messages API) — enabled when ANTHROPIC_API_KEY is set. Output is
    validated against the label set before it is accepted; a malformed response is an error,
    never a silent default.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("classify_batch: no ANTHROPIC_API_KEY — set the key, or set IC_AI=off for a deterministic run")
    import anthropic  # pinned in pyproject
    client = anthropic.Anthropic(api_key=key)
    payload = [{"i": i, "name": r["name_verbatim"], "address": r.get("address_verbatim", ""),
                "city": r.get("city_verbatim", ""), "state": r.get("state_verbatim", ""),
                "naics": r.get("naics_verbatim", "")} for i, r in enumerate(rows)]
    msg = client.messages.create(
        model=model, max_tokens=4000, temperature=temperature,
        system=prompt,
        messages=[{"role": "user", "content": "Classify each establishment. Return a JSON array of "
                   "{i, label, confidence, type, reason} with label in IC|NOT-IC|UNCERTAIN, "
                   "confidence 0-1, reason <= 12 words.\n\n" + json.dumps(payload)}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise RuntimeError("classify_batch: no JSON array in response")
    out = json.loads(m.group(0))
    if len(out) != len(rows):
        raise RuntimeError(f"classify_batch: {len(out)} labels for {len(rows)} rows")
    for o, r in zip(out, rows):
        if o.get("label") not in LABELS:
            raise RuntimeError(f"classify_batch: bad label {o.get('label')!r}")
        o["row_hash"] = r["row_hash"]
    return out


def run(rows: list[dict], cfg: dict, seeds: list[dict], cache_dir: Path, prompt_path: Path) -> dict:
    """Classify candidates + seeds. Returns labels keyed by row_hash and the seed scoring input."""
    prompt = prompt_path.read_text()
    model, temp, bs = cfg["model"], cfg["temperature"], cfg["batch_size"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    labels: dict[str, dict] = {}
    pool = rows + seeds
    for i in range(0, len(pool), bs):
        batch = pool[i:i + bs]
        k = batch_key(batch)
        cached = cache_dir / f"{k}.json"
        if cached.exists():
            res = json.loads(cached.read_text())
        else:
            res = classify_batch(batch, prompt, model, temp)
            cached.write_text(json.dumps(res))
        for o in res:
            labels[o["row_hash"]] = o
    return {"labels": labels, "model": model, "temperature": temp, "prompt_hash": prompt_hash(prompt_path),
            "n_candidates": len(rows), "n_seeds": len(seeds)}
