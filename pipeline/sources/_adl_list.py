"""Shared reader for ADL's own plant lists — the 4Ward study and the July list.

These are the only source in the pipeline that knows what a plant MAKES and how much of it: floor
area, annual throughput, utilisation, revenue, automation level, primary and secondary capability.
No public roster carries any of it. They are also the reason the control test was retired: 100% of
4Ward's rows and 68% of July's share a normalised name with a control row, because the control
list was built from them (registry/config.yaml -> control).

Three things this reader refuses to pass on, because each would become a number that looks
measured and is not:

  * `#DIV/0!` and friends. Spreadsheet errors, 15 of them in the July list's utilisation column.
    Carried through as text they read as data.
  * "NOT ESTIMABLE", "[link removed]" and a bare `0` throughput. Someone wrote those to say "we do
    not know"; a zero in a capacity column says "this plant makes nothing".
  * A row with no company name at all (BuildZ, Ramtech in the July list carry a row and no plant).

The `Sources` column is kept per row as `value_basis`, verbatim. It is the difference between a
figure from a site visit, one from a cited URL, and one an LLM guessed — the July list says "CS
ChatGPT" on ten rows and nothing at all on seventy-one. A warehouse where those three look the
same is a warehouse that cannot be audited, which is the whole point of the golden rule.

`Address` in both files is a CITY, not a street. These rows therefore carry no street_key and
tier as T0 leads until another source supplies an address — which is the honest shape: ADL's list
says a plant exists in Shelby, Alabama, not where in Shelby.
"""
from __future__ import annotations
import csv
import re
from pathlib import Path
from ._common import US_STATES, contract_row, pick, require

# Values that mean "unknown" and must never reach a numeric column.
NOT_A_VALUE = re.compile(r"#DIV/0!|#REF!|#N/A|#VALUE!|NOT ESTIMABLE|link removed", re.I)


def _num(v: str) -> str:
    """Digits only, or "" — and "" for anything that is not a real figure."""
    v = (v or "").strip()
    if not v or NOT_A_VALUE.search(v):
        return ""
    d = re.sub(r"[^0-9]", "", v.split(".")[0])
    return "" if not d or int(d) == 0 else d      # a zero capacity is an absent one, not a claim


def _txt(v: str) -> str:
    v = (v or "").strip()
    return "" if not v or NOT_A_VALUE.fullmatch(v) else v


def read(paths: list[Path], source: dict, *, drop_names: set[str] = frozenset()) -> list[dict]:
    files = pick(paths, ".csv")
    require(bool(files), paths[0] if paths else Path(source["id"]),
            "no CSV in the archived folder — upload the transcribed list there, then re-run")
    out, skipped_state, no_name, dropped = [], 0, 0, 0
    for path in sorted(files):
        for r in csv.DictReader(open(path, newline="", encoding="utf-8-sig", errors="replace")):
            name = _txt(r.get("company_name"))
            if not name:
                no_name += 1
                continue
            if name.lower() in drop_names:
                dropped += 1          # named in the registry as not a manufacturer
                continue
            st = (r.get("state") or "").strip().upper()
            if st and st not in US_STATES:
                skipped_state += 1    # BC and AB are not US plants
                continue
            out.append(contract_row(
                source, len(out) + 1, name=name,
                address="",                                    # the file's "Address" is a city
                city=_txt(r.get("address")).rstrip(","), state=st,
                # Falls back to the archived object when the registry names no url: source_url is
                # a required column, and run 35406498976 halted at Layer 2 with all 242 rows blank
                # because these entries had none. An internal document still has a place it lives.
                source_url=source.get("url") or f"blob://ic-sources/{source['id']}/{path.name}",
                source_document=path.name,
                source_identifier=f"{name}|{_txt(r.get('address'))}|{st}",
                notes=_txt(r.get("classification_notes"))[:400],
                phone=_txt(r.get("phone")), website=_txt(r.get("website")),
                sq_ft=_num(r.get("factory_size_sf")),
                adl_validated="1",
                primary_capability=_txt(r.get("primary_capability")),
                secondary_capability=_txt(r.get("secondary_capability")),
                material=_txt(r.get("material_used")), sector=_txt(r.get("sector_market")),
                throughput=_num(r.get("annual_throughput") or r.get("annual_max_throughput")),
                throughput_unit=_txt(r.get("throughput_unit")),
                utilisation_pct=_num(r.get("utlization_rate")),
                vacant_capacity=_num(r.get("vacant_capacity_pct") or r.get("vacant_capacity_sf")),
                annual_revenue_usd=_num(r.get("annual_revenue_usd")),
                automation_level=_txt(r.get("automation_level")),
                states_serviced=_txt(r.get("states_serviced")),
                country_based=_txt(r.get("country_based")),
                value_basis=_txt(r.get("sources"))[:300]))
    require(bool(out), files[0], "CSV read but no row carried a company name")
    out[0]["notes"] = (out[0]["notes"] + f" | ADL list {source['id']}: {len(out)} plants; "
                       f"{no_name} rows with no company, {skipped_state} non-US, "
                       f"{dropped} dropped as not a manufacturer")[:400]
    return out
