"""or_bcd — Oregon BCD Prefabricated Structures Program: registered manufacturers, one per manufacturing location.

Two routes, tried in order, both deterministic:
  1. BCD's licensing "registration data file" — the licence-holder search page
     (https://www.oregon.gov/bcd/licensing/pages/search.aspx) links a downloadable data file of
     all licences; rows whose licence/programme type mentions "Prefab" are kept.
  2. The programme's registered-manufacturers PDF
     (https://www.oregon.gov/bcd/permit-services/prefab/Documents/prefab-registered-manufacturers.pdf).
     Registry trap: this PDF has been observed to contain no data — so the parser requires rows.
If neither yields rows the run halts here (NeedsBrowser) with the licence search URL for a
Playwright sweep; nothing is guessed.
"""
from __future__ import annotations
import re
from pathlib import Path
from ._common import http_get, csv_rows, xlsx_rows, pdf_pages_text, contract_row, split_city_state_zip, LayoutChanged, NeedsBrowser

SEARCH_PAGE = "https://www.oregon.gov/bcd/licensing/pages/search.aspx"
LIST_PDF = "https://www.oregon.gov/bcd/permit-services/prefab/Documents/prefab-registered-manufacturers.pdf"
CSZ = re.compile(r"^(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$")

# The licence file names its address lines addr1..addr4, so a lookup for a column containing
# "address" or "street" found nothing and every row arrived with an empty address. addr1 is
# usually the street, but it also carries PO boxes and "ATTN:" lines with the real street pushed
# down to addr2, and addr4 merely repeats the city/state/zip that already have columns of their
# own. So the street is the first of addr1..addr3 that looks like one, and the rest is a mailing
# detail recorded in notes.
NOT_STREET = re.compile(r"^\s*(P\.?\s?O\.?\s?BOX|ATTN|C/O)\b", re.I)
IS_STREET = re.compile(r"^\s*\d+[A-Za-z]?\s+\S")


def _street(*lines: str) -> tuple[str, list[str]]:
    """First address line that is a street; everything else is a mailing detail."""
    lines = [l.strip() for l in lines if (l or "").strip()]
    for i, l in enumerate(lines):
        if IS_STREET.match(l) and not NOT_STREET.match(l):
            return l, lines[:i] + lines[i + 1:]
    return "", lines


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    paths = []
    try:
        page = http_get(SEARCH_PAGE, archive_dir, "search.html")
        html = page.read_text(encoding="utf-8", errors="replace")
        data = [h for h, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S)
                if re.search(r"data file|download", re.sub("<[^>]+>", " ", t), re.I) and re.search(r"\.(csv|xlsx?|zip|txt)(\?|$)", h, re.I)]
        if data:
            url = data[0] if data[0].startswith("http") else "https://www.oregon.gov" + data[0]
            paths.append(http_get(url, archive_dir, "bcd_licenses" + re.search(r"\.(csv|xlsx?|zip|txt)", url, re.I).group(0).lower()))
    except Exception as e:  # the data file is the preferred route, not the only one
        (archive_dir / "search.error.txt").write_text(str(e))
    paths.append(http_get(source.get("url") or LIST_PDF, archive_dir, "prefab-registered-manufacturers.pdf"))
    return paths


def parse(paths: list[Path], source: dict) -> list[dict]:
    out = []
    for path in paths:
        if path.suffix in (".csv", ".txt", ".xlsx"):
            rows = xlsx_rows(path) if path.suffix == ".xlsx" else csv_rows(path)
            for i, r in enumerate(rows, 1):
                blob = " ".join(str(v) for v in r.values()).lower()
                if "prefab" not in blob:
                    continue
                col = lambda *names: next((v for n in names for k, v in r.items() if k.lower() == n), "")
                get = lambda *ks: next((v for k, v in r.items() if any(x in k.lower() for x in ks)), "")
                # third-party inspectors and plan reviewers sit in the same file; they are not plants
                if re.match(r"\s*TPI\b", col("lictype", "license_type") or get("lictype"), re.I):
                    continue
                street, mailing = _street(col("addr1", "address1", "address"), col("addr2", "address2"), col("addr3", "address3"))
                state = col("state", "st") or get("state")
                dba = col("dba")
                expiry = _iso(col("expiration_date") or get("expir"))
                out.append(contract_row(source, i, name=col("full_name", "business_name") or get("business", "name", "licensee"),
                                        address=street, city=col("city") or get("city"),
                                        state=state, zip_code=col("zipcode", "zip") or get("zip"),
                                        source_url=SEARCH_PAGE, source_document=path.name,
                                        # a two-letter state is the only US marker this file carries; the
                                        # Canadian registrants leave it blank and put the province in addr2/3
                                        country="US" if re.fullmatch(r"[A-Za-z]{2}", state or "") else "",
                                        source_identifier=col("licnbr", "license_number") or get("license", "registration", "number"),
                                        status=col("lic_status") or get("status"), expiry_date=expiry,
                                        status_basis="dated_expiry" if expiry else None,
                                        notes="; ".join(x for x in ([f"dba={dba}"] if dba else []) + mailing)))
            if out:
                return out
        elif path.suffix == ".pdf":
            entries, cur = [], []
            for ln in "\n".join(pdf_pages_text(path)).splitlines():
                s = ln.strip()
                if not s or re.match(r"^(page \d|bcd |prefabricated structures program|registered manufacturers|updated|as of)", s, re.I):
                    continue
                cur.append(s)
                if CSZ.match(s):
                    entries.append(cur); cur = []
            for i, e in enumerate(entries, 1):
                m = CSZ.match(e[-1])
                addr = next((l for l in e[1:-1] if re.match(r"^\d+\s", l)), e[1] if len(e) > 2 else "")
                out.append(contract_row(source, i, name=e[0], address=addr, city=m.group(1), state=m.group(2), zip_code=m.group(3),
                                        source_url=LIST_PDF, source_document=path.name))
    if not out:
        raise NeedsBrowser(f"or_bcd: no rows from the data file or the list PDF (registry trap: the PDF is empty). "
                           f"Sweep the licence search with Playwright: {SEARCH_PAGE} (programme: Prefabricated Structures, active only).")
    return out


def _iso(s: str) -> str:
    from ._common import iso_date
    return iso_date(s)


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source)
