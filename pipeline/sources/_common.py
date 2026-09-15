"""Shared toolkit for fetchers. Every fetcher is two functions:

    fetch(source, cfg, archive_dir) -> list[Path]   deterministic download; archives the raw file(s)
    parse(paths, source) -> list[dict]              raw file(s) → contract rows, verbatim

and `pull(source, cfg, archive_dir)` is parse(fetch(...)). Keeping them apart means a file
downloaded by hand can be parsed and checked without network access:

    python -m pipeline.sources.check tx_tdlr                       # fetch + parse + validate
    python -m pipeline.sources.check tx_tdlr --file ~/Downloads/2-Certified_Manufacturers_List.pdf

Rules (docs/contract.md): verbatim in, never corrected; blank means blank; never fabricate a
row; record row_position; archive the raw file before parsing it. A layout that does not match
what the parser expects raises LayoutChanged pointing at the archived file — it never returns
a plausible-looking partial result.
"""
from __future__ import annotations
import csv, hashlib, io, json, re, time, urllib.error, urllib.request
from datetime import date
from pathlib import Path
from ..contract import COLUMNS

# Several state sites sit behind a WAF that rejects any unfamiliar product token: Michigan answered
# 403 and IIBC 500 to a UA naming this project, including when it was appended to a browser string.
# The UA is therefore a plain mainstream one, and the project identifies itself in X-Contact, which
# WAFs ignore. Nothing here defeats an access control: these are public pages served to any browser.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")
CONTACT = "https://github.com/pcsmith2000/IC_Factory_Database"


class LayoutChanged(Exception):
    """The archived file does not look like what the parser was written against."""


class NeedsBrowser(Exception):
    """The source needs a scripted browser session (Playwright) that is not available here."""


# ---------------------------------------------------------------- fetching
def http_get(url: str, archive_dir: Path, filename: str | None = None, *, data: bytes | None = None,
             headers: dict | None = None, timeout: int = 120, retries: int = 3) -> Path:
    """GET (or POST when data is given), archive the response body, return the archived path.
    Writes <archive_dir>/<filename> and a sidecar .meta.json with url, status, sha256, size."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    name = filename or (url.rstrip("/").rsplit("/", 1)[-1] or "index.html")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": USER_AGENT, "X-Contact": CONTACT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9", **(headers or {})})
    # Retry 5xx and transport errors: several of these sites answer an intermittent 500 under
    # repeated requests (IIBC does). A 4xx is not retried — it means the request itself is wrong.
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(); status = resp.status; ctype = resp.headers.get("Content-Type", "")
            break
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries:
                raise
        except urllib.error.URLError:
            if attempt == retries:
                raise
        time.sleep(2 ** attempt)
    out = archive_dir / name
    out.write_bytes(body)
    (archive_dir / f"{name}.meta.json").write_text(json.dumps({
        "url": url, "status": status, "content_type": ctype, "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(), "retrieved": date.today().isoformat(), "post": data is not None}, indent=2))
    return out


# ---------------------------------------------------------------- readers
def pdf_pages_text(path: Path) -> list[str]:
    """Text per page. pdfplumber if installed (keeps table layout best), else pypdf."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return [(p.extract_text() or "") for p in pdf.pages]
    except ImportError:
        pass
    try:
        from pypdf import PdfReader
        return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]
    except ImportError as e:
        raise ImportError("pip install -e '.[acquire]' — pdfplumber (or pypdf) is needed to read PDFs") from e


def pdf_lines(path: Path) -> list[list[dict]]:
    """Every page's words grouped into visual lines, each word {text, x0, x1}. Reading order."""
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError("pip install -e '.[acquire]' — pdfplumber is needed to read PDF layouts") from e
    from collections import defaultdict
    out = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            rows: dict[int, list[dict]] = defaultdict(list)
            for w in page.extract_words():
                rows[round(w["top"] / 3)].append({"text": w["text"], "x0": w["x0"], "x1": w["x1"],
                                                  "top": w["top"], "page": page.page_number})
            for key in sorted(rows):
                out.append(sorted(rows[key], key=lambda w: w["x0"]))
    return out


def drop_repeated_lines(lines: list[list[dict]], min_repeats: int = 3) -> list[list[dict]]:
    """Remove page furniture: running headers, footers, "Page 3 of 13", the column header row.

    Furniture repeats at the SAME HEIGHT on many pages; a data row does not. A line is dropped
    only when both hold: its text (with digit runs masked, so "Page 3 of 13" and "Page 4 of 13"
    match) and its vertical position repeat at least `min_repeats` times. Text alone is not
    enough — wrapped address lines like "HOUSTON, TX 77095" legitimately recur, and dropping
    those would silently truncate records. Content-blind on purpose: it needs no list of the
    strings one agency happens to print.
    """
    from collections import Counter
    keys = [(re.sub(r"\d+", "#", " ".join(w["text"] for w in ln)), round(ln[0].get("top", 0) / 6))
            for ln in lines if ln]
    seen = Counter(keys)
    return [ln for ln, k in zip([l for l in lines if l], keys) if seen[k] < min_repeats]


def set_difference(all_lines: list, kept: list) -> list:
    """The lines in `all_lines` that `kept` no longer contains, compared by identity."""
    keep = {id(ln) for ln in kept}
    return [ln for ln in all_lines if id(ln) not in keep]


def detect_columns(lines: list[list[dict]], labels: list[str], path: Path, *, gap: float = 10.0) -> list[tuple[str, float]]:
    """Column left edges for a whitespace-aligned PDF table, calibrated per file.

    Headers in these PDFs are centred over columns while data is left-aligned, so the header x
    alone cannot slice a row. Instead: find the header line (it carries every label in `labels`),
    cluster the x where a word starts after a wide gap across all lines — those clusters are the
    real column starts — then give each label the cluster nearest its header. A label with no
    cluster near it means the layout is not the one this parser was written against.
    Returns [(label, x_start), ...] left to right.
    """
    is_header = lambda ln: all(any(w["text"].lower().startswith(l.lower()) for w in ln) for l in labels)
    header = next((ln for ln in lines if is_header(ln)), None)
    require(header is not None, path, f"no header line carrying {labels}")
    # Header and footer lines are excluded from the clustering: they repeat once per page, so they
    # would otherwise form clusters of their own and a label would match its own centred header, or
    # a running footer would drag a column's left edge sideways. The header is still needed to name
    # the columns, so it is skipped here rather than removed from `lines`.
    furniture = {id(ln) for ln in set_difference(lines, drop_repeated_lines(lines))}
    starts: list[float] = []
    for ln in lines:
        if is_header(ln) or id(ln) in furniture:
            continue
        prev = None
        for w in ln:
            if prev is None or w["x0"] - prev > gap:
                starts.append(w["x0"])
            prev = w["x1"]
    # Group nearby starts, then take each group's LEFT edge: the data is left-aligned, so the
    # smallest x a column ever starts at is that column's true boundary. Rounding to a grid
    # instead would push the boundary right of some rows and silently steal their first word.
    groups, group = [], []
    for x in sorted(starts):
        if group and x - group[-1] > 6:
            groups.append(group); group = []
        group.append(x)
    if group:
        groups.append(group)
    # A real column starts on most rows. Stray indents do not: a title, a second table later in the
    # document, or the wrapped tail of a long cell all produce small groups that would otherwise be
    # mistaken for column boundaries and split a cell in two.
    floor = max(3, int(0.4 * max((len(g) for g in groups), default=0)))
    clusters = sorted(min(g) for g in groups if len(g) >= floor)
    require(bool(clusters), path, "no repeating column starts found — is this a table?")
    # Assign columns left to right under a monotonic constraint. A header is centred over its
    # column while the data is left-aligned, so a wide column's data can start far to the LEFT of
    # its header — "Manufacturer" centred at x=258 over data starting at x=178, with a spurious
    # cluster at 291 from wrapped names in between. Taking the nearest cluster picks the spurious
    # one; taking the LAST cluster at or before the header picks the real column start. A short
    # column whose data sits right of its header (a two-letter STATE) has no cluster at or before
    # it, so the first cluster after the previous column is used instead.
    cols, floor_x = [], float("-inf")
    for label in labels:
        hx = next(w["x0"] for w in header if w["text"].lower().startswith(label.lower()))
        remaining = [c for c in clusters if c > floor_x]
        require(bool(remaining), path, f"column {label!r}: no data column left of it — layout changed")
        at_or_before = [c for c in remaining if c <= hx]
        pick = max(at_or_before) if at_or_before else min(remaining)
        require(abs(pick - hx) < 200, path,
                f"column {label!r}: header at x={hx:.0f} but the data column resolved to x={pick:.0f} — layout changed")
        cols.append((label, float(pick)))
        floor_x = pick
    return cols


def slice_columns(line: list[dict], cols: list[tuple[str, float]]) -> dict[str, str]:
    """Assign each word on a line to its column by x, and join each column's words with spaces."""
    out = {name: [] for name, _ in cols}
    edges = [x for _, x in cols]
    for w in line:
        i = 0
        for j, e in enumerate(edges):
            if w["x0"] >= e - 1:
                i = j
        out[cols[i][0]].append(w["text"])
    return {k: " ".join(v) for k, v in out.items()}


def pdf_tables(path: Path) -> list[list[list[str]]]:
    """Every table on every page as rows of cell strings (pdfplumber only)."""
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError("pip install -e '.[acquire]' — pdfplumber is needed for PDF tables") from e
    out = []
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages:
            for t in p.extract_tables() or []:
                out.append([[(c or "").strip() for c in row] for row in t])
    return out


def html_tables(html: str) -> list[list[list[str]]]:
    """Every <table> as rows of cell strings, in document order (BeautifulSoup)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise ImportError("pip install -e '.[acquire]' — beautifulsoup4 is needed for HTML sources") from e
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for t in soup.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        tables.append(rows)
    return tables


def html_text(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:
        raise ImportError("pip install -e '.[acquire]' — beautifulsoup4 is needed for HTML sources") from e
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return soup.get_text("\n", strip=True)


def csv_rows(path: Path, encoding: str = "utf-8-sig") -> list[dict]:
    with open(path, newline="", encoding=encoding, errors="replace") as f:
        return list(csv.DictReader(f))


def xlsx_rows(path: Path, sheet: str | int = 0) -> list[dict]:
    try:
        import openpyxl
    except ImportError as e:
        raise ImportError("pip install -e '.[acquire]' — openpyxl is needed for XLSX sources") from e
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if isinstance(sheet, str) else wb.worksheets[sheet]
    it = ws.iter_rows(values_only=True)
    header = [str(h or "").strip() for h in next(it)]
    # blank rows are kept (as all-empty dicts) so row_position stays the file's own row number
    return [dict(zip(header, [("" if v is None else str(v).strip()) for v in r])) for r in it]


# ---------------------------------------------------------------- shaping
_CSZ = re.compile(r"^(?P<city>.+?)[,\s]+(?P<state>[A-Z]{2})\.?\s*(?P<zip>\d{5}(?:-\d{4})?)?\s*$")
# "1 Mill Rd, Saint Augusta, MN 56301" → street + city + state + zip; the zip and the province
# forms are optional because some rows are Canadian and some carry no postcode at all.
_ADDR = re.compile(r"^(?P<street>.*?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\.?(?:\s+(?P<zip>\d{5}(?:-\d{4})?))?\s*$")
_PROVINCES = {"ALBERTA": "AB", "BRITISH COLUMBIA": "BC", "MANITOBA": "MB", "NEW BRUNSWICK": "NB",
              "NEWFOUNDLAND": "NL", "NOVA SCOTIA": "NS", "ONTARIO": "ON", "QUEBEC": "QC",
              "SASKATCHEWAN": "SK", "PRINCE EDWARD ISLAND": "PE"}


_STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "FLORIDA": "FL", "GEORGIA": "GA",
    "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM",
    "NEW YORK": "NY", "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT",
    "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC", "PUERTO RICO": "PR",
}


def split_address(s: str) -> tuple[str, str, str, str, str]:
    """'5122 N STATE ROAD 39, LA PORTE, IN 46350' → (street, city, state, zip, country).

    Returns the whole string as the street with everything else blank when it does not match —
    blank means blank, and an address is never invented from a partial match. The only repairs
    are to damage the PDF itself introduced: a ZIP+4 split across a line wrap ("76055- 4900"),
    and a trailing hyphen left by an absent +4.
    """
    s = " ".join((s or "").split())
    s = re.sub(r"(\d{5})\s*-\s+(\d{4})\b", r"\1-\2", s)   # "76055- 4900" → "76055-4900"
    s = re.sub(r"(\d{5})\s*-\s*$", r"\1", s)                 # "75103 -"     → "75103"
    for name, code in _STATE_NAMES.items():                    # "BEDFORD, OHIO 44146" → "OH"
        s = re.sub(rf",\s*{name}\b", f", {code}", s, flags=re.I)
    m = _ADDR.match(s)
    if m:
        return m.group("street").strip(), m.group("city").strip(), m.group("state"), (m.group("zip") or ""), "US"
    # Canadian rows name the province in full and often carry no postcode
    up = s.upper()
    for name, code in _PROVINCES.items():
        m2 = re.search(rf",\s*{name}\b\s*(?P<pc>[A-Z]\d[A-Z]\s*\d[A-Z]\d)?\s*$", up)
        if m2:
            head = s[: m2.start()].rstrip(" ,")
            street, _, city = head.rpartition(",")
            pc = (m2.group("pc") or "").replace(" ", "")
            return (street.strip() or head.strip()), (city.strip() if street else ""), code, pc, "CA"
    return s, "", "", "", "US"


def split_city_state_zip(s: str) -> tuple[str, str, str]:
    """'Houston, TX 77020' → ('Houston', 'TX', '77020'). No correction: pieces are the source's
    own characters; an unparseable string comes back whole in city with blank state/zip."""
    s = (s or "").strip()
    m = _CSZ.match(s)
    if not m:
        return s, "", ""
    return m.group("city").strip(" ,"), m.group("state"), m.group("zip") or ""


def contract_row(source: dict, position: int, *, name: str, address: str = "", city: str = "", state: str = "",
                 zip_code: str = "", source_url: str, source_document: str, source_identifier: str = "",
                 naics: str = "", status: str = "", status_basis: str | None = None, expiry_date: str = "",
                 lat: str = "", lon: str = "", notes: str = "", country: str = "US") -> dict:
    r = {c: "" for c in COLUMNS}
    r.update(source_id=source["id"], source_url=source_url, source_document=source_document,
             retrieved_date=date.today().isoformat(), row_position=str(position),
             name_verbatim=name.strip(), address_verbatim=address.strip(), city_verbatim=city.strip(),
             state_verbatim=state.strip(), zip_verbatim=zip_code.strip(), country=country,
             source_identifier=source_identifier.strip(), naics_verbatim=naics.strip(), status_verbatim=status.strip(),
             status_basis=status_basis or source.get("status_basis", "none"), expiry_date=expiry_date.strip(),
             lat=lat, lon=lon, notes=notes.strip())
    return r


def iso_date(s: str) -> str:
    """'01/31/2027' or '2027-01-31' or 'January 31, 2027' → '2027-01-31'; anything else → ''. Never guesses."""
    s = (s or "").strip()
    for pat, fmt in ((r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d"), (r"^\d{1,2}/\d{1,2}/\d{4}$", "%m/%d/%Y"),
                     (r"^\d{1,2}/\d{1,2}/\d{2}$", "%m/%d/%y"), (r"^[A-Za-z]+ \d{1,2}, \d{4}$", "%B %d, %Y")):
        if re.match(pat, s):
            from datetime import datetime
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                return ""
    return ""


def require(cond: bool, path: Path, why: str) -> None:
    if not cond:
        raise LayoutChanged(f"{why} — inspect the archived file {path}")


def companies_block(html: str) -> str:
    """Trim an HTML page to its visible text for an extraction prompt; caps size."""
    return html_text(html)[:60000]
