"""The 19-column contract CSV and its validation. Layer 2 and every gate read only this."""
from __future__ import annotations
import csv, hashlib, re
from dataclasses import dataclass
from pathlib import Path

CONTRACT_VERSION = "1"
COLUMNS = [
    "source_id", "source_url", "source_document", "retrieved_date", "row_position",
    "name_verbatim", "address_verbatim", "city_verbatim", "state_verbatim", "zip_verbatim",
    "country", "source_identifier", "naics_verbatim", "status_verbatim", "status_basis",
    "expiry_date", "lat", "lon", "notes",
]
# Fields a source MAY carry beyond the contract, added 2026-09-17. Optional on read so every
# archived normalised CSV and the seeds file stay valid; blank by default on write. They are not in
# VERBATIM, so row_hash — and with it the classifier cache — is unchanged by their presence, and
# they play no part in the facility signature, so no id moves. Enrichment and GA DCA already carry
# all three in notes as prose; this is where they become queryable.
#   website           the plant's or company's site, as the source printed it
#   sq_ft             plant floor area in square feet, digits only, as the source stated it
#   operating_status  open | closed | revoked | unknown — a claim the source made, not an inference
#   phone             digits only, US 10-digit, as the source printed it (see phone_digits)
OPTIONAL = ["website", "sq_ft", "operating_status", "phone"]
ADDED = ["city_norm", "street_key", "state", "no_fixed_plant", "contract_version", "row_hash"]
REQUIRED = COLUMNS[:5]
STATUS_BASES = {"dated_expiry", "on_current_list", "explicit_status_field", "certified_as_of_date", "none"}
VERBATIM = COLUMNS[5:10]

def phone_digits(s: str) -> str:
    """A US phone reduced to its ten digits, or "" when it is not one.

    Stored as digits because the same plant is printed four ways across the rosters —
    "(662) 563-4574", "662-563-4574", "662.563.4574", "1-662-563-4574" — and a field that keeps
    the punctuation cannot be matched, deduplicated or joined on. Formatting is a rendering
    decision and belongs to whatever displays it.

    A leading country code 1 is dropped. Anything that is not ten digits after that is refused
    rather than truncated: an extension, a fax range, a partial OCR read and a ZIP+phone run
    together are all better absent than wrong, because a wrong number reaches a real stranger.
    """
    d = "".join(ch for ch in (s or "") if ch.isdigit())
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else ""


_CITY_MAP = {"st": "saint", "st.": "saint", "ste": "sainte", "mt": "mount", "ft": "fort"}
_STREET_SUFFIX = {"street": "st", "st.": "st", "avenue": "ave", "ave.": "ave", "road": "rd", "rd.": "rd",
                  "drive": "dr", "dr.": "dr", "boulevard": "blvd", "highway": "hwy", "lane": "ln",
                  "parkway": "pkwy", "court": "ct", "place": "pl", "north": "n", "south": "s",
                  "east": "e", "west": "w",
                  # Added 2026-09-17 from confirmed duplicate facilities in the deployed build.
                  "terrace": "ter", "terr": "ter", "circle": "cir", "trail": "trl", "square": "sq",
                  "turnpike": "tpke", "expressway": "expy", "freeway": "fwy", "route": "rte",
                  "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw"}
# "1505 W Third Ave" and "1505 W 3rd Ave" are one plant (Ess Metron, Denver) and were two
# facilities. Streets are numbered in words as often as in figures.
_ORDINAL_WORD = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th",
                 "sixth": "6th", "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th",
                 "eleventh": "11th", "twelfth": "12th", "thirteenth": "13th", "fourteenth": "14th",
                 "fifteenth": "15th", "sixteenth": "16th", "seventeenth": "17th",
                 "eighteenth": "18th", "nineteenth": "19th", "twentieth": "20th"}
# Street TYPES only. Directionals are deliberately excluded: "5980 W Sam Houston Pkwy N" ends in a
# type followed by a direction, and treating "n" as a type collapsed it to "5980 w sam houston n",
# merging it with a different address. A direction qualifies a street; it is not one.
_DIRECTIONALS = {"n", "s", "e", "w", "ne", "nw", "se", "sw"}
_STREET_TYPES = (set(_STREET_SUFFIX.values()) | {"st", "ave", "rd", "dr", "blvd", "hwy", "ln",
                                                 "pkwy", "ct", "pl", "ter", "cir", "trl", "sq",
                                                 "way"}) - _DIRECTIONALS


@dataclass
class ValidationError(Exception):
    source_id: str
    message: str
    def __str__(self): return f"[{self.source_id}] {self.message}"


def norm_city(city: str) -> str:
    """Collapse the city-string variances that produced every confirmed duplicate."""
    s = re.sub(r"[^a-z0-9 ]", " ", (city or "").lower())
    parts = [_CITY_MAP.get(p, p) for p in s.split()]
    return " ".join(parts)


def street_key(address: str) -> str:
    """Street number + normalised street name. Survives any city spelling."""
    s = re.sub(r"[^a-z0-9 ]", " ", (address or "").lower())
    parts = [_STREET_SUFFIX.get(p, _ORDINAL_WORD.get(p, p)) for p in s.split()]
    # A run of two street-type tokens is a transcription artifact: one register wrote Atkinson
    # Industries at "1801 E 27th St Terrace" and another at "1801 E 27th Terrace", and they became
    # two facilities at one address. Keep the LAST of the run — it is the street's actual type.
    collapsed = []
    for p in parts:
        if collapsed and p in _STREET_TYPES and collapsed[-1] in _STREET_TYPES:
            collapsed[-1] = p
        else:
            collapsed.append(p)
    parts = collapsed
    # drop a unit designator and everything after it ("suite 4", "unit b", "bldg 2")
    for i, p in enumerate(parts):
        if p in {"suite", "ste", "unit", "bldg", "building", "apt"}:
            parts = parts[:i]; break
    m = re.match(r"^(\d+[a-z]?)\s+(.+)$", " ".join(parts))
    if not m:
        return ""
    return f"{m.group(1)} {m.group(2)}".strip()


def row_hash(row: dict) -> str:
    h = hashlib.sha256()
    for c in [*VERBATIM, "source_id", "source_identifier"]:
        h.update((row.get(c) or "").encode()); h.update(b"\x1f")
    return h.hexdigest()[:16]


def read_contract(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None:
            raise ValidationError(path.stem, "empty file")
        missing = [c for c in COLUMNS if c not in r.fieldnames]
        if missing:
            raise ValidationError(path.stem, f"missing contract columns: {missing}")
        rows = list(r)
        for row in rows:
            for c in OPTIONAL:           # tolerated when absent on disk; present on every row in memory
                row.setdefault(c, "")
        return rows


def validate_rows(source_id: str, rows: list[dict]) -> list[str]:
    """Return a list of problems; empty means the file passes. Never mutates."""
    problems: list[str] = []
    for i, row in enumerate(rows, 1):
        for c in REQUIRED:
            if not (row.get(c) or "").strip():
                problems.append(f"row {i}: required column {c} is blank")
        if row.get("status_basis") not in STATUS_BASES:
            problems.append(f"row {i}: status_basis {row.get('status_basis')!r} not in {sorted(STATUS_BASES)}")
        if row.get("source_id") != source_id:
            problems.append(f"row {i}: source_id {row.get('source_id')!r} != {source_id!r}")
        if row.get("expiry_date") and not re.match(r"^\d{4}-\d{2}-\d{2}$", row["expiry_date"]):
            problems.append(f"row {i}: expiry_date not ISO (dates as strings never compare)")
        if len(problems) > 50:
            problems.append("... more"); break
    return problems


_US = {"AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
       "ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR",
       "PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"}
_US_NAMES = {"alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA","colorado":"CO",
    "connecticut":"CT","delaware":"DE","district of columbia":"DC","florida":"FL","georgia":"GA","hawaii":"HI",
    "idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS","kentucky":"KY","louisiana":"LA",
    "maine":"ME","maryland":"MD","massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS",
    "missouri":"MO","montana":"MT","nebraska":"NE","nevada":"NV","new hampshire":"NH","new jersey":"NJ",
    "new mexico":"NM","new york":"NY","north carolina":"NC","north dakota":"ND","ohio":"OH","oklahoma":"OK",
    "oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC","south dakota":"SD",
    "tennessee":"TN","texas":"TX","utah":"UT","vermont":"VT","virginia":"VA","washington":"WA",
    "west virginia":"WV","wisconsin":"WI","wyoming":"WY"}


def us_state(verbatim: str | None) -> str:
    """A US state code, or nothing. Never a code invented by truncation.

    This used to be `.upper()[:2]`, which turned every non-US region a source carried into a
    plausible US state: MBI's Turku, Finland ("Varsinais-Suomi") became VA, Belo Horizonte ("Minas
    Gerais") became MI, Dubai ("Dubayy") became DU, Shanghai became SH. Sixty of MBI's 188 members
    are abroad and every one of them landed in the warehouse with a state it never had — two of
    them with states that exist, where they could match a control row and pollute per-state
    coverage. A state this database did not read is a state it must not report.
    """
    s = " ".join((verbatim or "").split())
    if not s:
        return ""
    if s.upper() in _US:
        return s.upper()
    return _US_NAMES.get(s.lower(), "")


def normalise(rows: list[dict]) -> list[dict]:
    """Add the Layer 2 columns. Verbatim columns are untouched."""
    out = []
    for row in rows:
        r = dict(row)
        r["city_norm"] = norm_city(row.get("city_verbatim", ""))
        r["street_key"] = street_key(row.get("address_verbatim", ""))
        r["state"] = us_state(row.get("state_verbatim"))
        r["no_fixed_plant"] = "NO-FIXED-PLANT" in (row.get("notes") or "").upper()
        r["contract_version"] = CONTRACT_VERSION
        r["row_hash"] = row_hash(row)
        out.append(r)
    return out


def write_rows(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    cols = columns or (COLUMNS + OPTIONAL + ADDED)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
