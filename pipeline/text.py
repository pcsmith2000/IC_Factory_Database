"""Text normalisation shared across the pipeline.

Only helpers whose callers had byte-identical copies live here. Anything that feeds a facility
signature or IC-number (reconcile.norm_name, contract.norm_city, facility_intake's suffix list)
keeps its own definition: changing one of those changes which plants are the same plant.
"""
from __future__ import annotations
import re

_NOT_ALNUM = re.compile(r"[^a-z0-9]")


def alnum_lower(value) -> str:
    """Lower-case, every character but a-z and 0-9 removed; None is ''. For equality tests that
    should ignore case, spacing and punctuation ("St. Louis" == "st louis")."""
    return _NOT_ALNUM.sub("", str(value or "").lower())
