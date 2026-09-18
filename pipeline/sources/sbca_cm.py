"""sbca_cm — SBCA Component Manufacturer members, the truss segment's only roster.

The Structural Building Components Association is the trade body for the plants that make wood
trusses, wall panels and floor systems, and its Component Manufacturer membership IS a list of
those plants. That segment is invisible to every regulator source the pipeline holds: a truss
plant needs no state licence, files no HUD registration and appears in EPA FRS only if it happens
to hold an environmental permit. 29 of the never-ingested control rows are Wood Structural
Components and this is the only place they are written down.

sbcindustry.com does not answer a crawler at all — not login-gated, no HTTP status, curl exit 000
(probed 2026-09-17, recorded in the registry). The roster reached the pipeline instead as a
transcription of SBCA's own "Current CM Members" BatchGeo map, uploaded by hand into the blob, so
this source is `acquire: blob-only` and has no fetcher. 932 rows, 868 of them US.

Membership category is the classification: an SBCA *Component Manufacturer* is a component
manufacturer, the same argument that lets SIPA's manufacturing roster carry `needs_classify:
false`. The rows never reach Layer 3.

What the coordinates are, and are not. Every row carries a lat/lon and every row claims
`accuracy=ROOFTOP`, because that is what BatchGeo reports whatever it actually resolved. They are
geocodes of the member's MAILING address, which for a multi-plant company is its head office. The
transcriber's own evidence note says so on every row and it rides into `notes` verbatim. They are
carried because a coordinate a source stated is evidence, and ranked last for `lat_lon` by
survivorship, so stage 10's real rooftop geocode replaces them the moment it runs.
"""
from __future__ import annotations
from pathlib import Path
from . import _operator_csv


def parse(paths: list[Path], source: dict) -> list[dict]:
    return _operator_csv.read(paths, source, note="SBCA Component Manufacturer members")


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    raise NotImplementedError("sbca_cm is blob-only: upload the transcribed CSV, then re-run")
