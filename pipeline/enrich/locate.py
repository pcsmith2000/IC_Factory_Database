"""Stage 9 — find a plant address for a facility that has only a name and a city.

The model is never asked to recall an address. It is given a web search tool and must return the
address together with the URL it came from and the sentence it read there; an answer without a
citation is discarded rather than stored (gate E1). That distinction is the whole design: a
recalled plant address is confidently wrong often enough to poison a field nothing downstream can
second-guess, and no consumer of golden_facility can tell a recalled value from a read one.

Search goes through the Vercel AI Gateway, which exposes Anthropic's native web search over the
Messages API — the same SDK path layer 3's classifier already uses, so provider resolution stays
in pipeline.ai_client_and_model and only the tool definition is new here.

Reach: without search this stage could only extract from pages some source already published a URL
for, which is about 9% of the facilities that need an address. With search it is not bounded that
way, but recall is still an open question until measured against control/seeds.csv.
"""
from __future__ import annotations
import json, re

# The gateway documents Anthropic's basic server tool for the Messages API. Newer model families
# take web_search_20260209 with dynamic filtering; change this constant, not the call site.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 4}
DEFAULT_MODEL = "anthropic/claude-sonnet-5"

PROMPT = """Find the street address of the manufacturing plant below.

Company: {name}
City/State the plant is licensed in: {city}, {state}

Rules:
- Search the web. Do not answer from memory.
- Return the address of the PLANT, not a head office, mailing address or PO box, and not a
  dealership or sales office.
- The address must appear verbatim on a page you actually opened.
- If you cannot find it, or you are not confident the page refers to this company's plant in this
  city, return found=false. Returning false is correct and expected; guessing is not.

Reply with only this JSON:
{{"found": true|false,
  "address": "street address only, no city/state/zip",
  "city": "", "state": "", "zip": "",
  "source_url": "the page the address appeared on",
  "quote": "the sentence from that page containing the address",
  "confidence": 0.0-1.0,
  "reason": "one short sentence"}}"""


class LocateUnavailable(RuntimeError):
    pass


def _client(model: str):
    try:
        from .. import ai_client_and_model
    except ImportError as e:      # provided by layers 1-8; this stage does not define its own
        raise LocateUnavailable("pipeline.ai_client_and_model is missing — it comes from the "
                                "layers 1-8 branch; merge it before running stage 9") from e
    return ai_client_and_model(model)


def _extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _searched_urls(blocks) -> set[str]:
    """Every URL the search tool actually returned, so a citation can be checked against them."""
    urls = set()
    for b in blocks:
        content = getattr(b, "content", None)
        if isinstance(content, list):
            for item in content:
                u = getattr(item, "url", None)
                if u:
                    urls.add(u)
    return urls


def locate_one(row: dict, client, model: str) -> dict:
    """One facility. Returns the parsed answer plus the URLs search actually visited."""
    msg = client.messages.create(
        model=model, max_tokens=1500, tools=[WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": PROMPT.format(
            name=row.get("name", ""), city=row.get("city", ""), state=row.get("state", ""))}])
    text = "".join(getattr(b, "text", "") for b in msg.content)
    return {"answer": _extract_json(text) or {}, "visited": _searched_urls(msg.content)}


def run(rows: list[dict], model: str = DEFAULT_MODEL, client=None,
        min_confidence: float = 0.7) -> dict:
    """rows: facilities with no address. Returns assertions plus a report."""
    from ._db import assertion
    from ..contract import street_key
    if client is None:
        client, model, _provider = _client(model)
    asserts, rejected = [], []
    for row in rows:
        try:
            got = locate_one(row, client, model)
        except Exception as e:                       # one bad facility must not fail the stage
            rejected.append({"facility_id": row["facility_id"], "why": f"{type(e).__name__}: {e}"})
            continue
        a, visited = got["answer"], got["visited"]
        addr = (a.get("address") or "").strip()
        url = (a.get("source_url") or "").strip()
        quote = (a.get("quote") or "").strip()
        conf = a.get("confidence")
        why = None
        if not a.get("found"):
            why = a.get("reason") or "model reported not found"
        elif not street_key(addr):
            why = f"not a street address: {addr!r}"
        elif not url or not quote:
            why = "no citation"                      # E1: a citation is mandatory
        elif visited and url not in visited:
            # the model may only cite a page search actually opened
            why = f"cited a page search did not visit: {url}"
        elif not isinstance(conf, (int, float)) or conf < min_confidence:
            why = f"confidence {conf} below {min_confidence}"
        if why:
            rejected.append({"facility_id": row["facility_id"], "why": why})
            continue
        asserts.append(assertion(row["facility_id"], "address", addr,
                                 source_id="enrich:locate", basis="web_cited",
                                 confidence=float(conf), evidence=f"{url} :: {quote[:300]}"))
    return {"requested": len(rows), "assertions": asserts, "located": len(asserts),
            "rejected": rejected, "model": model}
