"""lead_addresses — street addresses found for plants the database already holds as T0 leads.

A lead is a named plant with no placeable address: 1,456 of run 26's 4,287 facilities. The
enrichment pass looks each one up — the company's own site, a trade-press item, a chamber listing —
and what it finds is transcribed into the same operator schema the state-registry uploads use, with
`source_url` naming where the street came from and `evidence` saying what kind of page it was.

Two things this source is NOT, and the difference is the whole point:

It is not a way to put the CONTROL LIST into the database. A row here must be a plant a real
source already surfaced; the lookup adds a street to a facility that exists, it does not add a
facility because the control names one. Rows that entered this way would make recall self-
fulfilling, and G4 exists to refuse rows with control provenance. Prioritising the leads the control
names first is fine — they are still our rows — inventing rows for control names is not.

It is not a parser. Nothing here fetches anything; a person or a bounded lookup pass fills the CSV,
and each row's `source_url` is the provenance a reader checks. An address read off a search result
is an assertion, and it is written down as one.

Mechanically: a row with a street forms an addressed cluster in Layer 5, and the existing T0 lead
folds into it through the same name+city+state pass that merges any roster's addressless row into
its plant. The lead's signature retires; the addressed cluster takes the id. So `located` moves and
`found` does not, which is exactly the shape an address-only enrichment should have.
"""
from __future__ import annotations
from pathlib import Path
from . import _operator_csv


def parse(paths: list[Path], source: dict) -> list[dict]:
    return _operator_csv.read(paths, source, note="street addresses found for existing T0 leads")
