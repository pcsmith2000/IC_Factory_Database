"""IC Factory Database — fixed end-to-end ingestion pipeline (v4 process, v1.0 code)."""
import os, re

__version__ = "1.0.0"


def ai_enabled() -> bool:
    """False when IC_AI is off/0/none — the deterministic run.

    The two AI steps (Layer 3 classification, Layer 1 `ai_extraction` sources) are then skipped
    and recorded as skipped, never silently defaulted: a skipped classifier drops no row on its
    own judgement, it routes the whole candidate set to the review queue. Deliberate switch only
    — a missing AI_GATEWAY_API_KEY with IC_AI on is still a loud failure, not a quiet degrade.
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


def ai_client_and_model(model: str) -> tuple[object, str, str]:
    """(client, model_id, provider) for the Vercel AI Gateway — the one AI provider.

    The gateway serves the Anthropic Messages API, so the `anthropic` SDK is still the client
    here; only the base URL and the spelling of the model id differ from talking to Anthropic
    directly. The SDK is not the credential, and keeping it is not keeping a second provider.

    There used to be a fallback to a first-party ANTHROPIC_API_KEY when the gateway key was
    absent, and it was removed on 2026-09-18 with the move to the company Vercel team. It was a
    silent-divergence path: a run that lost the gateway key kept going against a different
    provider, on a differently-spelled model id, and only the `provider` field of the run record
    said so. Two credentials also had to be carried, rotated and kept in step to buy that. A
    missing key is now one loud failure with one thing to fix.
    """
    import anthropic  # pinned in pyproject; imported here so the module loads without it

    key = os.environ.get("AI_GATEWAY_API_KEY")
    if not key:
        raise RuntimeError("no AI_GATEWAY_API_KEY — set it, or set IC_AI=off for a "
                           "deterministic run")
    # The SDK default of 2 retries is not enough against the gateway's burst limiting: Layer 1
    # sends one extraction call per archived page — 126 of them for corporate_locations — with no
    # retry of its own, and a single 429 partway through fails the source and halts the whole run.
    # Retries here cover every caller, classifier and extractor alike.
    return (anthropic.Anthropic(api_key=key, base_url=AI_GATEWAY_BASE_URL, max_retries=8),
            gateway_model_id(model), "vercel_ai_gateway")
