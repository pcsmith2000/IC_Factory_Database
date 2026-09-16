"""Layer 3: candidate generation (deterministic) + classification (AI, regimented).

Only sources with `needs_classify: true` pass through here. The classifier is the frozen prompt
in prompts/CLASSIFIER-PROMPT.md; the model id, temperature and prompt hash are recorded in the
run record. Batches are keyed by content hash so a re-run is a no-op for unchanged rows.
Seeded positives/negatives are hidden in every batch and scored by gate G5.

The provider is resolved once, in pipeline.ai_client_and_model: the Vercel AI Gateway when
AI_GATEWAY_API_KEY is set, else the Anthropic API directly. Both speak the Messages API, so
`classify_batch` is unchanged by the choice; only the model id is spelled differently.
"""
from __future__ import annotations
import hashlib, json, math, os, re
from pathlib import Path

from . import ai_client_and_model

LABELS = {"IC", "NOT-IC", "UNCERTAIN"}

# keyword × NAICS-family matrix. The pairing carries the signal — neither alone.
# COMPONENT is near-zero precision unless paired with 3212xx/3219xx. OFFSITE is permit language: excluded.
KEYWORD_NAICS = {
    r"\bmodular\b":            {"3219", "3212", "3323", "2362", "2381", "4233"},
    r"\bprefab":               {"3219", "3212", "3323", "2362", "2381", "4233"},
    r"\bmanufactured hom":     {"3219"},
    r"\bpanel(s|ized|ised)?\b":{"3219", "3212", "3323"},
    r"\btruss":                {"3212", "3219", "2362"},
    r"\bprecast\b":            {"3273", "3272"},
    r"\bmass timber|\bglulam|\bclt\b|\bcross.laminated": {"3212", "3211"},
    r"\bsip(s)?\b|\bstructural insulated": {"3219", "3212"},
    r"\bmetal building|\bpre.?engineered": {"3323"},
    r"\bcomponent":            {"3212", "3219"},
    r"\bvolumetric|\bpod(s)?\b": {"3219", "3323"},
    # Named after the product, in families the other keywords never reach. "Building systems" is
    # what a pre-engineered metal building manufacturer calls itself — NCI, Ceco, Schulte and
    # Schulte's neighbours are all 3323 and all invisible to every rule above.
    r"\bwall system":          {"3323", "3273", "2362", "2381", "4233", "3211"},
    r"\bbuilding system":      {"3323", "3273", "2362", "2381", "4233", "3211"},
    # Deliberately narrow. `structural` in 3323 is mostly steel fabricators, and fabricated
    # structural steel for a building IS a building system — but so is a bridge girder, and the
    # name alone cannot tell them apart. That judgement is the classifier's, which is the point:
    # this rule buys the rows a hearing, it does not label them.
    r"\bstructural\b":         {"3323", "2362"},
}
PLACENAME_COLLISIONS = {"trussville", "old forge", "campanello"}

# Families admitted WHOLE, without asking what the row is called. 3219 (other wood product
# manufacturing) and 3212 (veneer, plywood and engineered wood) are where the truss, panel and
# component plants this database exists to find actually sit, and the name filter was discarding
# them on the strength of their names: Shelter Systems, Toll Integrated Systems and Pacific Wall
# Systems are all on the validated list, all in these families, and none of them says what it
# makes in its name (docs/epa-coverage.md). Real manufacturers are named after people and places.
#
# This costs ~10,000 extra rows on the EPA slice, taking the classifier from ~2,900 to ~13,000 and
# a run from $0.06 to ~$0.27 — cheap against a filter making a silent, untested judgement before
# the judgement. The placename guard does NOT apply here: it exists to stop a keyword firing on a
# town called Trussville, and in these families the name is not the reason for admission.
WIDE_NAICS_FAMILIES = {"3219", "3212"}


def candidates(rows: list[dict], core_naics: set[str]) -> list[dict]:
    """Return rows in a core NAICS code, in a wide family, or matching keyword × NAICS family."""
    out = []
    for r in rows:
        naics = (r.get("naics_verbatim") or "").strip()
        name = (r.get("name_verbatim") or "").lower()
        if naics in core_naics:
            r["_candidate_reason"] = f"core naics {naics}"; out.append(r); continue
        if naics[:4] in WIDE_NAICS_FAMILIES:
            r["_candidate_reason"] = f"wide family {naics[:4]}"; out.append(r); continue
        if any(p in name for p in PLACENAME_COLLISIONS):
            continue
        for pat, fams in KEYWORD_NAICS.items():
            if re.search(pat, name) and naics[:4] in fams:
                r["_candidate_reason"] = f"{pat} × {naics[:4]}"; out.append(r); break
    return out


def prompt_hash(prompt_path: Path) -> str:
    return hashlib.sha256(prompt_path.read_bytes()).hexdigest()[:12]


def batch_key(rows: list[dict], model: str = "", prompt: str = "") -> str:
    """Cache key for one batch. Includes the model and the prompt, not just the rows.

    Keying on row hashes alone meant a cached result was reused after the model or the frozen
    prompt changed — the run record would name the new model while the labels came from the old
    one, and a model comparison would silently score whichever model ran first.
    """
    h = hashlib.sha256()
    for r in rows:
        h.update(r["row_hash"].encode())
    h.update(b"\x1f"); h.update(model.encode())
    h.update(b"\x1f"); h.update(hashlib.sha256(prompt.encode()).digest())
    return h.hexdigest()[:16]


def batches(rows: list[dict], seeds: list[dict], size: int) -> list[list[dict]]:
    """Split candidates into batches with the seeds spread evenly through them.

    `rows + seeds` chunked in order piles every seed into the final batch — with the candidate
    pool at 2,922 rows and 60 seeds that last batch was 73% seeds, so the graded rows were the
    only ones the model ever saw in seed-dense context, and the audit measured a situation no real
    batch is in. Deal both round-robin instead, so each batch carries its share, then order each
    batch by row_hash: deterministic (the cache key depends on it) but uncorrelated with which
    rows are seeds.
    """
    n = max(1, math.ceil((len(rows) + len(seeds)) / size))
    out: list[list[dict]] = [[] for _ in range(n)]
    for i, r in enumerate(rows):
        out[i % n].append(r)
    for i, sd in enumerate(seeds):
        out[i % n].append(sd)
    return [sorted(b, key=lambda r: r["row_hash"]) for b in out if b]


def classify_batch(rows: list[dict], prompt: str, model: str, temperature: float,
                   usage_out: dict | None = None) -> list[dict]:
    """One regimented model call. Returns [{row_hash, label, confidence, type, reason}] in row order.

    Goes to whichever provider pipeline.ai_client_and_model resolves — the Vercel AI Gateway when
    AI_GATEWAY_API_KEY is set, else the Anthropic API — over the Messages API either way. Output
    is validated against the label set before it is accepted; a malformed response is an error,
    never a silent default.
    """
    client, model, _ = ai_client_and_model(model)
    payload = [{"i": i, "name": r["name_verbatim"], "address": r.get("address_verbatim", ""),
                "city": r.get("city_verbatim", ""), "state": r.get("state_verbatim", ""),
                "naics": r.get("naics_verbatim", "")} for i, r in enumerate(rows)]
    # ~30 output tokens per row (label, confidence, type, a <=12-word reason). A flat 4000 left a
    # 100-row batch ~25% headroom, and overflow truncates the JSON array into a hard error.
    # temperature goes through extra_body: the Anthropic SDK dropped it from messages.create()
    # (current first-party models reject sampling parameters outright), but the gateway's Messages
    # API still documents and honours it, and determinism is worth having on a classifier.
    user = ("Classify each establishment. Return a JSON array of "
            "{i, label, confidence, type, reason} with label in IC|NOT-IC|UNCERTAIN, "
            "confidence 0-1, reason <= 12 words.\n\n" + json.dumps(payload))
    # A model that reasons before answering spends the budget thinking first: nemotron-nano wrote
    # 12,990 characters of deliberation and hit the ceiling before the array, gpt-5-nano returned
    # nothing at all. Both looked like broken output contracts and were really a ceiling set too
    # low. Output tokens bill for what is generated, so headroom is free where it goes unused.
    want = min(32000, max(16000, 64 * len(rows) + 1000))
    for attempt in range(2):
        try:
            msg = client.messages.create(model=model, max_tokens=want,
                                         extra_body={"temperature": temperature},
                                         system=prompt, messages=[{"role": "user", "content": user}])
            break
        except Exception as e:
            # Output ceilings are per model and the gateway only says so on rejection — that floor
            # started 400ing nova-lite, whose cap is 10000. Take the limit out of the refusal and
            # retry once, rather than make every caller carry a per-model table.
            cap = re.search(r"model limit of (\d+)", str(e))
            if attempt or not cap:
                raise
            want = int(cap.group(1))
    if usage_out is not None and getattr(msg, "usage", None) is not None:
        usage_out["input_tokens"] = usage_out.get("input_tokens", 0) + (msg.usage.input_tokens or 0)
        usage_out["output_tokens"] = usage_out.get("output_tokens", 0) + (msg.usage.output_tokens or 0)
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        # Say what came back. "No JSON array" alone cannot distinguish a model that refused, one
        # that wrote prose, one that returned a bare object, and one that was cut off at
        # max_tokens — and those call for different fixes.
        raise RuntimeError(f"classify_batch: no JSON array in response "
                           f"(stop_reason={getattr(msg, 'stop_reason', '?')}, {len(text)} chars): "
                           f"{text[:180]!r}")
    out = json.loads(m.group(0))
    if len(out) != len(rows):
        raise RuntimeError(f"classify_batch: {len(out)} labels for {len(rows)} rows")
    # Map by the index the model echoes back, never by position. Zipping the response onto the
    # batch assumes an ordering the model was only asked for, and a model that answers all 100 in
    # a different order would have every label attached to the wrong establishment — right count,
    # valid labels, nothing raised. Requiring i to be a permutation makes that a loud failure.
    seen: set[int] = set()
    for o in out:
        i = o.get("i")
        if not isinstance(i, int) or not 0 <= i < len(rows):
            raise RuntimeError(f"classify_batch: index {i!r} outside 0..{len(rows) - 1}")
        if i in seen:
            raise RuntimeError(f"classify_batch: index {i} returned twice")
        seen.add(i)
        if o.get("label") not in LABELS:
            raise RuntimeError(f"classify_batch: bad label {o.get('label')!r}")
        o["row_hash"] = rows[i]["row_hash"]
    return out


def run(rows: list[dict], cfg: dict, seeds: list[dict], cache_dir: Path, prompt_path: Path) -> dict:
    """Classify candidates + seeds. Returns labels keyed by row_hash and the seed scoring input."""
    prompt = prompt_path.read_text()
    model, temp, bs = cfg["model"], cfg["temperature"], cfg["batch_size"]
    cache_dir.mkdir(parents=True, exist_ok=True)
    labels: dict[str, dict] = {}
    for batch in batches(rows, seeds, bs):
        k = batch_key(batch, model, prompt)
        cached = cache_dir / f"{k}.json"
        if cached.exists():
            res = json.loads(cached.read_text())
        else:
            res = classify_batch(batch, prompt, model, temp)
            cached.write_text(json.dumps(res))
        for o in res:
            labels[o["row_hash"]] = o
    _, resolved, provider = ai_client_and_model(model)
    return {"labels": labels, "model": resolved, "provider": provider, "temperature": temp,
            "prompt_hash": prompt_hash(prompt_path), "n_candidates": len(rows), "n_seeds": len(seeds)}
