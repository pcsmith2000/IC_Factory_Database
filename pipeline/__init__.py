"""IC Factory Database — fixed end-to-end ingestion pipeline (v4 process, v1.0 code)."""
import os

__version__ = "1.0.0"


def ai_enabled() -> bool:
    """False when IC_AI is off/0/none — the deterministic run.

    The two AI steps (Layer 3 classification, Layer 1 `ai_extraction` sources) are then skipped
    and recorded as skipped, never silently defaulted: a skipped classifier drops no row on its
    own judgement, it routes the whole candidate set to the review queue. Deliberate switch only
    — a missing ANTHROPIC_API_KEY with IC_AI on is still a loud failure, not a quiet degrade.
    """
    return os.environ.get("IC_AI", "").lower() not in ("off", "0", "none")
