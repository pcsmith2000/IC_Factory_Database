"""IC Factory Database — fixed end-to-end ingestion pipeline (v4 process, v1.0 code)."""
import os, re

__version__ = "1.0.0"


def ai_enabled() -> bool:
    """False when IC_AI is off/0/none — the deterministic run.

    The two AI steps (Layer 3 classification, Layer 1 `ai_extraction` sources) are then skipped
    and recorded as skipped, never silently defaulted: a skipped classifier drops no row on its
    own judgement, it routes the whole candidate set to the review queue. Deliberate switch only
    — a missing ANTHROPIC_API_KEY with IC_AI on is still a loud failure, not a quiet degrade.
    """
    return os.environ.get("IC_AI", "").lower() not in ("off", "0", "none")


AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh"
# The two APIs spell the same model differently: first-party ids dash the minor version
# ("claude-haiku-4-5"), the gateway's catalogue dots it and qualifies it by provider
# ("anthropic/claude-haiku-4.5"). Sending one form to the other API is a 404, so translate
# rather than string-concatenate a prefix. Verified against GET /v1/models on the gateway.
_MINOR = re.compile(r"-(\d+)-(\d+)$")


def gateway_model_id(model: str) -> str:
    """First-party or bare id -> the gateway's provider-qualified, dotted id."""
    if "/" in model:
        return model                      # already qualified — how a non-Anthropic model is pinned
    return "anthropic/" + _MINOR.sub(r"-\1.\2", model)


def anthropic_model_id(model: str) -> str:
    """Gateway id -> the first-party id."""
    return model.split("/", 1)[-1].replace(".", "-")


def ai_client_and_model(model: str) -> tuple[object, str, str]:
    """(client, model_id, provider) for whichever AI provider is configured.

    Vercel AI Gateway when AI_GATEWAY_API_KEY is set, else the Anthropic API directly. The
    gateway serves the Anthropic Messages API, so the SDK and every call site are unchanged —
    only the base URL and the spelling of the model id differ.
    """
    import anthropic  # pinned in pyproject; imported here so the module loads without it

    # The SDK default of 2 retries is not enough against the gateway's burst limiting: Layer 1
    # sends one extraction call per archived page — 126 of them for corporate_locations — with no
    # retry of its own, and a single 429 partway through fails the source and halts the whole run.
    # Retries here cover every caller, classifier and extractor alike.
    gateway = os.environ.get("AI_GATEWAY_API_KEY")
    if gateway:
        return (anthropic.Anthropic(api_key=gateway, base_url=AI_GATEWAY_BASE_URL, max_retries=8),
                gateway_model_id(model), "vercel_ai_gateway")
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no AI_GATEWAY_API_KEY and no ANTHROPIC_API_KEY — set one, "
                           "or set IC_AI=off for a deterministic run")
    return anthropic.Anthropic(api_key=key, max_retries=8), anthropic_model_id(model), "anthropic"
