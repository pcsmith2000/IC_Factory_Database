"""Stage 9 — find a plant address for a facility that has only a name and a city.

The model is never asked to recall an address. It is given a web search tool and must return the
address together with the URL it came from and the sentence it read there; an answer without a
citation is discarded rather than stored (gate E1). That distinction is the whole design: a
recalled plant address is confidently wrong often enough to poison a field nothing downstream can
second-guess, and no consumer of golden_facility can tell a recalled value from a read one.

Search goes through the Vercel AI Gateway, and deliberately not through one provider's own tool.
The gateway's `vercel:*_search` server tools run with ANY model it serves, so stage 9 is not tied
to a frontier vendor to get search — which is the whole reason the pipeline is on a gateway. Two
paths exist and `--search` picks between them:

    gateway   Chat Completions + vercel:parallel_search   any model, search $5/1000
    native    Messages API + web_search_20250305          Anthropic models only, search $10/1000

Search, not the model, is the dominant cost here. Against a cheap open-weight model the token bill
is a rounding error and roughly 90% of what remains is the per-search charge, so the provider of
the search matters more than the provider of the model. That is why the default path is the one
that costs $5 rather than $10, and why the model is a switch rather than a decision baked in.

Reach: without search this stage could only extract from pages some source already published a URL
for, which is about 9% of the facilities that need an address. With search it is not bounded that
way, but recall is still an open question until measured against control/seeds.csv.
"""
from __future__ import annotations
import json, re
from datetime import date

# The gateway documents Anthropic's basic server tool for the Messages API. Newer model families
# take web_search_20260209 with dynamic filtering; change this constant, not the call site.
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 4}

GATEWAY_CHAT_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
MAX_RESULTS = 5

# Each tool takes a different config shape, so the builder lives with the name rather than the call
# site guessing. Tako is the one with a trap: it searches a curated data graph as well as the web,
# and inlining data rows is billed per row. Stage 9 wants a street address off a web page, so it
# asks for `sources.web` only and never sets includeContents — which also keeps it on the flat
# per-request price.
def _tako(objective):
    return {"query": objective, "effort": "fast", "sources": {"web": {"count": MAX_RESULTS}}}


SEARCH_TOOLS = {
    "parallel":   ("vercel:parallel_search",   lambda o: {"objective": o, "max_results": MAX_RESULTS}),
    "perplexity": ("vercel:perplexity_search", lambda o: {"query": o, "max_results": MAX_RESULTS}),
    "exa":        ("vercel:exa_search",        lambda o: {"query": o, "num_results": MAX_RESULTS}),
    "tako":       ("vercel:tako_search",       _tako),
}

# Search is ~90% of what stage 9 costs, so the search provider is the price, not the model.
# Tako is free on the gateway through 2026-09-30 and $7/1000 after; Parallel and Perplexity are
# $5/1000 flat. Defaulting by date rather than by a constant someone has to remember means the run
# is free while free and cheapest-paid afterwards, without a silent bill on October 1st. The choice
# is recorded in every run summary, so a run is always auditable for which it used.
TAKO_FREE_UNTIL = date(2026, 9, 30)


def default_search(today: date | None = None) -> str:
    return "tako" if (today or date.today()) <= TAKO_FREE_UNTIL else "parallel"


DEFAULT_SEARCH = default_search()
# An open-weight model, because the gateway is what makes that possible and stage 9 has no reason
# to pay frontier prices: the task is bounded extraction — open a page, return a street address or
# found=false — behind gates that discard anything uncited, unverified against the fetched page, or
# under 0.7 confidence. A weaker model's failures are REJECTED, not stored, which makes the cheap
# model the one to justify replacing rather than the one to justify trying.
#
# qwen3.7-flash rather than the very cheapest: $0.03/$0.13 per Mtok, tool-use, a 991k context that
# swallows search results without truncation, and it scored 100/97 in layers 1-8's bake-off without
# the instability that failed gpt-oss-120b and nova-lite there. That is a starting point, not a
# finding — layers 1-8 learned the hard way that a bake-off predicts little about production, so
# the number that settles it is cost per LOCATED address measured on a real --sample.
#
# Cost per call and cost per located address can move in opposite directions: search is billed per
# search and is model-independent, so a model that halves the token bill while halving the yield
# costs more. Both are recorded per run. `--model` and `--search` switch either without a code
# change, so the comparison is a workflow input rather than a commit.
DEFAULT_MODEL = "alibaba/qwen3.7-flash"

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


AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh"


def _client(model: str):
    """Prefer the shared resolver from layers 1-8; fall back to the gateway contract directly.

    `pipeline.ai_client_and_model` is the one place provider choice belongs, and once the layers
    1-8 branch lands it wins here automatically. Until then this stage would be unrunnable, so it
    falls back to the same contract that helper implements: the Vercel AI Gateway serves Anthropic's
    Messages API, so the SDK is unchanged and only the base URL and the model id spelling differ —
    the gateway qualifies by provider and dots the minor version (anthropic/claude-sonnet-5).
    """
    try:
        from .. import ai_client_and_model
        return ai_client_and_model(model)
    except ImportError:
        pass
    import os
    import anthropic
    gateway = os.environ.get("AI_GATEWAY_API_KEY")
    if gateway:
        return (anthropic.Anthropic(api_key=gateway, base_url=AI_GATEWAY_BASE_URL, max_retries=8),
                model if "/" in model else f"anthropic/{model}", "vercel_ai_gateway")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LocateUnavailable("stage 9 needs AI_GATEWAY_API_KEY (or ANTHROPIC_API_KEY); "
                                "set --limit 0 to skip the AI stage entirely")
    return (anthropic.Anthropic(api_key=key, max_retries=8),
            model.split("/", 1)[-1].replace(".", "-"), "anthropic")


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


def search_objective(row: dict) -> str:
    """The search the gateway runs for this facility.

    Built here rather than left to the model. The gateway applies a tool's `config` as a developer
    default that overrides model-generated values, so a static objective would search for the same
    thing 1,181 times. Building it per row turns that from a hazard into a feature: the search is
    deterministic given the row, which is the property the rest of this pipeline is built on.
    """
    where = ", ".join(x for x in (row.get("city") or "", row.get("state") or "") if x)
    return (f"street address of the {row.get('name', '')} manufacturing plant"
            + (f" in {where}" if where else "")).strip()


class GatewayError(RuntimeError):
    """Carries the HTTP status so _exhausted() can tell a spent key from a bad facility."""
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        super().__init__(f"Error code: {status_code} - {body[:300]}")


def locate_one_gateway(row: dict, model: str, key: str, search: str = DEFAULT_SEARCH,
                       timeout: int = 120) -> dict:
    """One facility, via Chat Completions and a gateway search tool — any model, any provider.

    The gateway runs the search itself and returns only the final answer, so unlike the Messages
    path there is no list of URLs search actually opened and the `visited` check cannot run. That
    check caught 1 rejection in 48; the check that does the work — fetching the cited page and
    confirming the address is on it — is independent of all this and unaffected.
    """
    import urllib.error, urllib.request
    tool_type, build_config = SEARCH_TOOLS[search]
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": PROMPT.format(
            name=row.get("name", ""), city=row.get("city", ""), state=row.get("state", ""))}],
        "tools": [{"type": tool_type, "config": build_config(search_objective(row))}],
        "tool_choice": "required",       # the prompt forbids answering from memory; enforce it
        "max_tokens": 1500,
    }).encode()
    req = urllib.request.Request(GATEWAY_CHAT_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise GatewayError(e.code, e.read().decode("utf-8", "replace")) from None
    return parse_gateway_response(out)


def parse_gateway_response(out: dict) -> dict:
    """Pull the answer, the search count and the token usage out of a Chat Completions reply."""
    choice = (out.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    usage = out.get("usage") or {}
    # the gateway reports what it ran here; there is no raw search result to read
    meta = ((msg.get("provider_metadata") or {}).get("gateway") or {})
    calls = meta.get("gatewayToolCalls")
    searches = len(calls) if isinstance(calls, list) else (calls or 0)
    return {"answer": _extract_json(text) or {}, "visited": set(),
            "usage": {"input_tokens": usage.get("prompt_tokens", 0) or 0,
                      "output_tokens": usage.get("completion_tokens", 0) or 0,
                      "web_searches": searches}}


def locate_one(row: dict, client, model: str) -> dict:
    """One facility. Returns the parsed answer plus the URLs search actually visited."""
    msg = client.messages.create(
        model=model, max_tokens=1500, tools=[WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": PROMPT.format(
            name=row.get("name", ""), city=row.get("city", ""), state=row.get("state", ""))}])
    text = "".join(getattr(b, "text", "") for b in msg.content)
    u = getattr(msg, "usage", None)
    # Searches are billed per search and are model-independent, so they are the part of the cost
    # a cheaper model does not reduce. Counting them separately is what makes "is Sonnet worth it"
    # answerable: at a low enough yield, a cheap model costs more per address than an expensive one.
    searches = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    return {"answer": _extract_json(text) or {}, "visited": _searched_urls(msg.content),
            "usage": {"input_tokens": getattr(u, "input_tokens", 0) or 0,
                      "output_tokens": getattr(u, "output_tokens", 0) or 0,
                      "web_searches": searches}}


def _page_states_the_address(url: str, address: str, timeout: int = 20) -> tuple[bool | None, str]:
    """Fetch the cited page, look for the address, and return the text around it.

    The snippet is returned rather than the model's own quote because auditing the first real run
    showed two of five quotes were page furniture — "Door Shop Store Details Store Locator Change
    My Store" offered as the sentence containing 36 McCoy St. The address checked out; the evidence
    a human would read did not. Taking the surrounding text from the page makes the stored quote
    something the page actually says, instead of the model's claim about what it says.

    Checking that search visited a URL proves the page exists, not that it says what the model
    claims — the model can open a real page and attribute an address to it that is not there, and
    no amount of prompting reliably prevents that. The address itself is checked rather than the
    quoted sentence, because a paraphrased quote is a formatting difference while a missing address
    is the actual error. A page that cannot be fetched is not evidence of dishonesty, so it returns
    None and the caller decides.
    """
    import re as _re, urllib.error, urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2_000_000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None, ""
    # decode entities before stripping tags: a snippet reading "&nbsp;/&nbsp;&nbsp;Store Details"
    # is stored as evidence and read by a person, and &nbsp; is not what the page says
    import html as _html
    text = _re.sub(r"\s+", " ", _html.unescape(_re.sub(r"<(script|style)[^>]*>.*?</\1>", " ",
                                                       body, flags=_re.S | _re.I)))
    text = _re.sub(r"\s+", " ", _re.sub(r"<[^>]+>", " ", text))
    norm = lambda s: _re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    page, want = norm(text), norm(address)

    def around(hay: str, needle: str) -> str:
        i = hay.find(needle)
        if i < 0:
            return ""
        return hay[max(0, i - 90):i + len(needle) + 90].strip()

    if want and want in page:
        # locate it in the readable text, not the normalised form, so the snippet is legible
        m = _re.search(_re.escape(address), text, _re.I)
        return True, (around(text, m.group(0)) if m else around(page, want))
    # street number plus the distinctive word of the street name, for "1200 Industrial Blvd" vs
    # "1200 Industrial Boulevard" — a suffix spelling difference is not a fabricated address
    m = _re.match(r"^(\d+)\s+(.*)$", want)
    if m:
        num, rest = m.group(1), m.group(2).split()
        distinctive = max(rest, key=len) if rest else ""
        if distinctive:
            # search the readable text, not the normalised form: the snippet is stored as evidence
            # and read by a person, so "1200 Industrial Boulevard" beats "1200 industrial boulevard"
            hit = _re.search(rf"\b{num}\b[^.]{{0,40}}\b{_re.escape(distinctive)}\w*\b", text, _re.I)
            if hit:
                return True, around(text, hit.group(0))
    return False, ""


USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# One bad facility must not fail the stage; one spent credential must not be mistaken for 200 of
# them. These statuses are facts about the key, not about the plant being searched for: the next
# facility will fail identically, so the stage stops and defers the rest rather than grinding
# through its whole ceiling recording verdicts no model ever reached.
#
# This is not hypothetical. The first pass against the release database attempted 200 facilities
# and reported 180 rejections; 152 of those were a 402 "API key budget exceeded" after the gateway
# key hit its $10 limit 48 facilities in. The run looked like a 10% success rate. It was 42%.
# 404 belongs here for the same reason: a model id the gateway does not serve is a fact about the
# configuration, and without it a typo in --model would be recorded as 200 facilities the model
# considered and declined — the exact failure this set exists to prevent, wearing a different hat.
FATAL_STATUSES = {401, 402, 403, 404}


def _exhausted(e: Exception) -> str:
    """The message, if this error means the credential is spent or refused; "" if not."""
    import re
    code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if code is None:
        m = re.search(r"Error code: (\d{3})", str(e))
        code = int(m.group(1)) if m else None
    return str(e) if code in FATAL_STATUSES else ""


def _reason(why: str) -> str:
    """Bucket a rejection so a run says where the funnel leaks, not just that it leaked.

    The difference that matters is between "search could not find this plant" and "verification
    would not accept what it found" — the first is a reach problem and the second is a strictness
    one, and they call for opposite fixes.
    """
    for needle, bucket in (
            ("not a street address", "not a street address"),
            ("no citation", "no citation"),
            ("cited a page search did not visit", "cited a page search never opened"),
            ("below", "confidence below threshold"),
            ("the cited page does not contain", "page did not contain the address"),
            ("could not read the cited page", "page could not be read"),
    ):
        if needle in why:
            return bucket
    return "model found nothing it could cite"


def run(rows: list[dict], model: str = DEFAULT_MODEL, client=None,
        min_confidence: float = 0.7, verify_page: bool = True,
        search: str = DEFAULT_SEARCH) -> dict:
    """rows: facilities with no address. Returns assertions plus a report.

    `search` picks the path: "native" is Anthropic's own tool over the Messages API and needs an
    Anthropic model; anything in SEARCH_TOOLS is the gateway's, and works with any model it serves.
    """
    import os
    from ._db import assertion
    from ..contract import street_key
    # An explicit client is a Messages client, so it selects the path it can actually drive.
    if client is not None:
        search = "native"
    if search != "native" and search not in SEARCH_TOOLS:
        raise LocateUnavailable(
            f"unknown --search {search!r}: expected native or one of {sorted(SEARCH_TOOLS)}")
    if search == "native":
        if client is None:
            client, model, _provider = _client(model)
        call = lambda row: locate_one(row, client, model)
    else:
        key = os.environ.get("AI_GATEWAY_API_KEY")
        if not key:
            raise LocateUnavailable(
                f"--search {search} goes through the Vercel AI Gateway and needs "
                "AI_GATEWAY_API_KEY; use --search native for the Anthropic path, or --limit 0 "
                "to skip the AI stage entirely")
        call = lambda row: locate_one_gateway(row, model, key, search)
    asserts, rejected = [], []
    attempted, stopped = 0, ""
    usage = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}
    for row in rows:
        attempted += 1
        try:
            got = call(row)
        except Exception as e:
            stopped = _exhausted(e)
            if stopped:
                attempted -= 1                       # this one never reached the model either
                break
            rejected.append({"facility_id": row["facility_id"], "why": f"{type(e).__name__}: {e}"})
            continue
        for k, v in (got.get("usage") or {}).items():
            usage[k] = usage.get(k, 0) + v
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
        elif verify_page:
            # the check E1 cannot make from the response alone: does that page really say this?
            states, snippet = _page_states_the_address(url, addr)
            if states is False:
                why = f"the cited page does not contain {addr!r}"
            elif states is None:
                why = f"could not read the cited page to confirm it: {url}"
            elif snippet:
                quote = snippet         # what the page says, not what the model said it says
        if why:
            rejected.append({"facility_id": row["facility_id"], "why": why})
            continue
        asserts.append(assertion(row["facility_id"], "address", addr,
                                 source_id="enrich:locate", basis="web_cited",
                                 confidence=float(conf), evidence=f"{url} :: {quote[:300]}"))
    import collections
    return {"requested": len(rows), "attempted": attempted, "assertions": asserts,
            "located": len(asserts), "rejected": rejected, "model": model, "search": search,
            "deferred": len(rows) - attempted,
            "budget_exhausted": bool(stopped), "budget_message": stopped[:300],
            "rejected_by_reason": dict(collections.Counter(_reason(r["why"]) for r in rejected)),
            "yield_pct": round(100 * len(asserts) / max(1, attempted), 1),
            "usage": usage,
            "usage_per_located": {k: round(v / max(1, len(asserts)), 1) for k, v in usage.items()}}
