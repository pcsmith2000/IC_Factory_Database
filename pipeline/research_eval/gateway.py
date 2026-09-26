"""AI Gateway calls, catalog prices, and the per-pass cost ceiling.

Every call is priced twice: `billed` is what the gateway reports it charged (usage.cost), and
`list` is tokens at the catalog price plus every search at its list price, even while a search is
free (Tako through 2026-09-30). The ceiling and the evaluation ledger use list price, so a pass can
never spend more than it says, and the production projection is honest after a promotion ends.

The ceiling (design section 1, control 2): before a facility starts, the spend so far plus the worst
case for one more facility must stay within max_cost_usd, or the pass stops starting facilities.
"""
from __future__ import annotations
import json, os, time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CATALOG_URL = "https://ai-gateway.vercel.sh/v1/models"
CHAT_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
# List price per search request (design section 5). Tako fast is free on the gateway through
# 2026-09-30 and $7/1000 after; the ledger counts it at $7/1000 throughout.
SEARCH_LIST_USD = {"tako": 0.007, "parallel": 0.005, "perplexity": 0.005, "exa": 0.007}
SEARCH_TOOLS = {
    "tako": ("vercel:tako_search", lambda q, n: {"query": q, "effort": "fast", "sources": {"web": {"count": n}}}),
    "parallel": ("vercel:parallel_search", lambda q, n: {"objective": q, "max_results": n}),
    "perplexity": ("vercel:perplexity_search", lambda q, n: {"query": q, "max_results": n}),
    "exa": ("vercel:exa_search", lambda q, n: {"query": q, "num_results": n}),
}
# Models above this line need the user's approval (the loop prompt's hard limit).
PRICE_LINE_PER_M = {"input": 2.0, "output": 10.0}


class BudgetExceeded(RuntimeError):
    pass


class GatewayError(RuntimeError):
    def __init__(self, status: int, body: str):
        self.status = status
        super().__init__(f"gateway HTTP {status}: {body[:300]}")


def load_catalog(path: str | None = None) -> dict:
    if path and os.path.exists(path):
        return json.load(open(path))
    with urlopen(CATALOG_URL, timeout=30) as r:
        return json.load(r)


def prices(catalog: dict, models: list[str]) -> dict[str, dict]:
    """Per-token input/output list prices for these models, from the catalog as read today."""
    out = {}
    by_id = {m["id"]: m for m in catalog.get("data", [])}
    for mid in models:
        m = by_id.get(mid)
        if not m or "pricing" not in m:
            raise ValueError(f"{mid} is not in the gateway catalog")
        p = m["pricing"]
        # Price at the dearest published rate for this model (a US-regional rate can be higher).
        regional = [r for r in (p.get("regional") or {}).values() if isinstance(r, dict)]
        inp = max([float(p["input"])] + [float(r.get("input", 0)) for r in regional])
        outp = max([float(p["output"])] + [float(r.get("output", 0)) for r in regional])
        out[mid] = {"input": inp, "output": outp, "per_million": {"input": round(inp * 1e6, 4), "output": round(outp * 1e6, 4)}}
    return out


def over_price_line(p: dict) -> bool:
    return p["per_million"]["input"] > PRICE_LINE_PER_M["input"] or p["per_million"]["output"] > PRICE_LINE_PER_M["output"]


class Meter:
    """Running cost for one pass, and the ceiling check."""

    def __init__(self, max_cost_usd: float, model_prices: dict[str, dict]):
        if not 0 < max_cost_usd <= 1.0:
            raise ValueError("max_cost_usd must be in (0, 1.00]")
        self.max = max_cost_usd
        self.prices = model_prices
        self.billed = 0.0
        self.list = 0.0
        self.searches = 0
        self.cached_searches = 0
        self.calls = 0
        self.tokens = {"input": 0, "output": 0}
        self.missing_cost = 0
        self.records: list[dict] = []

    def call_list_usd(self, model: str, input_tokens: int, output_tokens: int) -> float:
        p = self.prices[model]
        return input_tokens * p["input"] + output_tokens * p["output"]

    def worst_case(self, plan: dict) -> float:
        """List-price worst case for one more facility under this configuration."""
        usd = plan.get("searches", 0) * max(SEARCH_LIST_USD.values())
        for model, inp, outp, n in plan.get("calls", []):
            usd += n * self.call_list_usd(model, inp, outp)
        return usd

    def can_start(self, plan: dict) -> bool:
        return self.list + self.worst_case(plan) <= self.max

    def record(self, kind: str, model: str, usage: dict, facility_id: str, searches: int = 0,
               provider: str | None = None) -> dict:
        inp, outp = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        billed = usage.get("cost")
        list_usd = self.call_list_usd(model, inp, outp) + searches * SEARCH_LIST_USD.get(provider or "", 0.0)
        if billed is None:
            self.missing_cost += 1
            billed = list_usd                              # count the list price when the gateway omits cost
        rec = {"kind": kind, "facility_id": facility_id, "model": model, "input_tokens": inp, "output_tokens": outp,
               "searches": searches, "provider": provider, "billed_usd": round(float(billed), 8),
               "list_usd": round(list_usd, 8), "at": time.time()}
        self.billed += float(billed); self.list += list_usd
        self.calls += 1; self.searches += searches
        self.tokens["input"] += inp; self.tokens["output"] += outp
        self.records.append(rec)
        return rec

    def summary(self) -> dict:
        return {"max_cost_usd": self.max, "billed_usd": round(self.billed, 6), "list_usd": round(self.list, 6),
                "calls": self.calls, "searches": self.searches, "cached_searches": self.cached_searches,
                "tokens": self.tokens, "responses_missing_cost": self.missing_cost}


def chat(payload: dict, key: str | None = None, timeout: int = 150, retries: int = 3) -> dict:
    key = key or os.environ.get("AI_GATEWAY_API_KEY")
    if not key:
        raise GatewayError(0, "AI_GATEWAY_API_KEY is not set")
    req = Request(CHAT_URL, data=json.dumps(payload).encode(), method="POST",
                  headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise GatewayError(e.code, body) from None
        except (URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise GatewayError(0, str(e)) from None
        time.sleep(3 * 2 ** attempt)
    raise GatewayError(0, "unreachable")


def gateway_searches(raw: dict, tool: str) -> int:
    """Searches the gateway reports it ran (provider_metadata.gateway.gatewayToolCalls)."""
    msg = ((raw.get("choices") or [{}])[0]).get("message") or {}
    calls = ((msg.get("provider_metadata") or {}).get("gateway") or {}).get("gatewayToolCalls")
    if isinstance(calls, dict):
        v = calls.get(tool.split(":")[-1], calls.get(tool))
        if isinstance(v, int):
            return v
        return sum(x for x in calls.values() if isinstance(x, int))
    if isinstance(calls, list):
        return len(calls)
    return 0


def content(raw: dict) -> str:
    return (((raw.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
