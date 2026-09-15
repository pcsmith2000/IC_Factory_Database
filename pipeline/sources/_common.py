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
import csv, hashlib, io, json, re, urllib.request
from datetime import date
from pathlib import Path
from ..contract import COLUMNS

USER_AGENT = "Mozilla/5.0 (compatible; ic-factory-database/1.0; +https://github.com/pcsmith2000/IC_Factory_Database)"


class LayoutChanged(Exception):
    """The archived file does not look like what the parser was written against."""


class NeedsBrowser(Exception):
    """The source needs a scripted browser session (Playwright) that is not available here."""


# ---------------------------------------------------------------- fetching
def http_get(url: str, archive_dir: Path, filename: str | None = None, *, data: bytes | None = None,
             headers: dict | None = None, timeout: int = 120) -> Path:
    """GET (or POST when data is given), archive the response body, return the archived path.
    Writes <archive_dir>/<filename> and a sidecar .meta.json with url, status, sha256, size."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    name = filename or (url.rstrip("/").rsplit("/", 1)[-1] or "index.html")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(); status = resp.status; ctype = resp.headers.get("Content-Type", "")
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
