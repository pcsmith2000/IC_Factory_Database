"""AI extraction for prose pages (sources with `ai_extraction: true`).

One regimented call per page: the frozen prompt in prompts/EXTRACTION-PROMPT.md, the model
pinned in registry/config.yaml (classifier.model — one pinned model per run), temperature 0,
a JSON schema on the output. The raw response is archived beside the page, and every returned
name / address / city is checked verbatim against the archived page text: a value that is not
on the page is dropped and counted, never kept. Model id and prompt hash go in the sidecar so
the run record can carry them.
"""
from __future__ import annotations
import hashlib, json, os, re
from pathlib import Path

from .. import ai_client_and_model

SCHEMA = {
    "type": "object",
    "properties": {"locations": {"type": "array", "items": {
        "type": "object",
        "properties": {k: {"type": "string"} for k in ("name", "address", "city", "state", "zip", "kind", "evidence")},
        "required": ["name", "address", "city", "state", "zip", "kind", "evidence"], "additionalProperties": False}}},
    "required": ["locations"], "additionalProperties": False,
}


def prompt_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def verbatim_check(loc: dict, page_text: str) -> list[str]:
    """Fields whose value does not appear on the page (whitespace-insensitive, case-insensitive)."""
    hay = _norm_ws(page_text)
    return [k for k in ("name", "address", "city") if loc.get(k) and _norm_ws(loc[k]) not in hay]


def extract_locations(page_text: str, *, company: str, page_url: str, cfg: dict, prompt_path: Path, archive_to: Path) -> dict:
    """Returns {"locations": [...kept...], "dropped": [...], "model": ..., "prompt_hash": ..., "cached": bool}."""
    prompt = prompt_path.read_text()
    model, temperature = cfg["classifier"]["model"], cfg["classifier"]["temperature"]
    client, model, _provider = ai_client_and_model(model)
    cache_key = hashlib.sha256((prompt + model + page_text).encode()).hexdigest()[:16]
    archive_to.mkdir(parents=True, exist_ok=True)
    raw_path = archive_to / f"extract_{cache_key}.json"
    if raw_path.exists():
        raw = json.loads(raw_path.read_text()); cached = True
    else:
        resp = client.messages.create(
            model=model, max_tokens=16000, temperature=temperature, system=prompt,
            messages=[{"role": "user", "content": f"Company: {company}\nPage: {page_url}\n\nPAGE TEXT:\n{page_text}"}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if resp.stop_reason not in ("end_turn", "stop_sequence"):
            raise RuntimeError(f"extract_locations: stop_reason={resp.stop_reason} for {page_url}")
        text = next(b.text for b in resp.content if b.type == "text")
        raw = {"model": model, "prompt_hash": prompt_hash(prompt_path), "page_url": page_url, "response": json.loads(text),
               "usage": {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens}}
        raw_path.write_text(json.dumps(raw, indent=1)); cached = False
    kept, dropped = [], []
    for loc in raw["response"].get("locations", []):
        bad = verbatim_check(loc, page_text)
        (dropped if bad else kept).append({**loc, "_not_on_page": bad} if bad else loc)
    return {"locations": kept, "dropped": dropped, "model": raw["model"], "prompt_hash": raw["prompt_hash"], "cached": cached,
            "raw_path": str(raw_path)}
