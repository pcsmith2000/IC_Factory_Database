"""ny_dos — New York manufactured-housing certified manufacturers, from the state open-data API.

Most rows carry a city and no street, so most of this source lands at T0: named plants awaiting an
address. That is the honest tier for it and the reason recall is reported lead-inclusive AND
located separately.
"""
from __future__ import annotations
from pathlib import Path
from . import _operator_csv


def parse(paths: list[Path], source: dict) -> list[dict]:
    return _operator_csv.read(paths, source, note="NY DOS certified manufacturers")
