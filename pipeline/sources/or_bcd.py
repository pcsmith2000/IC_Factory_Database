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
                # The licence-search export names its columns licnbr / full_name / addr1..addr4 /
                # zipcode / lic_status / expiration_date — no column contains "address" or "number",
                # so matching only on those silently yielded blank addresses and blank ids.
                get = lambda *ks: next((v for k, v in r.items() if any(x in k.lower() for x in ks)), "")
                out.append(contract_row(source, i, name=get("business", "name", "licensee"), address=get("addr", "address", "street"), city=get("city"),
                                        state=get("state"), zip_code=get("zip"), source_url=SEARCH_PAGE, source_document=path.name,
                                        source_identifier=get("licnbr", "license", "registration", "number"), status=get("status"),
                                        expiry_date=_iso(get("expir")), status_basis="dated_expiry" if _iso(get("expir")) else None))
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
