"""ca_hcd — California HCD occupational licence holders of type Manufacturer.

Form-gated Salesforce search, transcribed by hand and uploaded to the blob. The registry's own trap
is the thing to keep hold of: HCD issues no plant-level approval number, so these are COMPANY-level
leads, and the address on the row is the licence address — often an out-of-state head office, which
is why "A & A Sheet Metal Products" appears with an Indiana street on a California licence.
"""
from __future__ import annotations
from pathlib import Path
from . import _operator_csv


def parse(paths: list[Path], source: dict) -> list[dict]:
    return _operator_csv.read(paths, source,
                              note="CA HCD occupational licence holders, Manufacturer, Active")
