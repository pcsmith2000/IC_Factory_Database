"""nc_osfm — North Carolina OSFM modular manufacturers.

Like Indiana, this is NOT a North Carolina list: the state registers every manufacturer selling
into it, so the roster is national and most rows are marked "County=Out of State".

The folder holds two files in the same schema and only one is manufacturers. The other is the
approved third-party inspection agencies — PFS TECO, ICC-NTA — which certify plants and do not
operate them. They are dropped on their own evidence line rather than by filename, so renaming the
upload cannot quietly publish nine certification bodies as modular factories.
"""
from __future__ import annotations
from pathlib import Path
from . import _operator_csv


def parse(paths: list[Path], source: dict) -> list[dict]:
    return _operator_csv.read(paths, source, drop_if_evidence="NOT a modular manufacturer",
                              note="NC OSFM modular manufacturers (national roster)")
