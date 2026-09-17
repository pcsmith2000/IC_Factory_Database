"""corporate_locations — location pages of multi-site truss / component companies (class C, ai_extraction).

The page list lives in the registry entry (`pages:`), one per company:
    - company: UFP Site Built
      url: https://ufpsitebuilt.com/our-locations
      follow: {href: "/our-locations/", text: ""}      # optional: also fetch linked sub-pages whose href/text match
Fetch is deterministic (pages archived with their hash); extraction is one regimented model call
per page (pipeline/sources/_extract.py, frozen prompt, pinned model, JSON schema) and every value
is checked verbatim against the archived page text — a value not on the page is dropped and
counted in the pull sidecar, never kept. Registry traps: prose pages, extraction only; this is a
roster of a biased sample, not a regulator.

A company entry with no url is reported (not silently skipped) and yields no rows.
"""
from __future__ import annotations
import json, re, time, urllib.parse
from pathlib import Path
from ._common import http_get, html_text, contract_row, require
from ._extract import extract_locations
from ..reconcile import norm_name

ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT = ROOT / "prompts" / "EXTRACTION-PROMPT.md"
MAX_FOLLOW = 150


# "Sumner, WA" · "Medford, OR" — a label that is only a place, not a plant name.
PLACE_LABEL = re.compile(r"^[A-Za-z .'\-]{2,28},\s*[A-Za-z]{2}\.?$")


def _qualify(company: str, label: str) -> str:
    """The establishment's name: the company, plus the site label only when that label names a SITE.

    84 Lumber's page names plants "Mt. Airy Truss Plant" and "Kings Mountain Truss Plant" — the
    site, never the operator — so the row reached the warehouse with nothing tying it to 84 Lumber.
    The company goes in front of a label like that, because the label is the only thing separating
    one 84 Lumber plant from another and this database is plant-level.

    But many pages label a plant with nothing but its town. The Truss Company lists "Sumner, WA",
    "Eugene, OR", "Centralia, WA", and qualifying those produced facilities called "The Truss
    Company — Sumner, WA". That is worse than useless: norm_name strips "The" and "Company", so the
    control row "The Truss Company" normalises to "truss" while the facility normalises to
    "trusssumnerwa", and all eight plants — every one of them correctly extracted, with its street
    address — failed to match anything. The town is already in the city and state columns, which is
    where the rest of the pipeline looks for it, and Layer 4's signature keeps the plants apart on
    exactly that. So a pure place label is DROPPED and the company name stands alone.
    """
    c, l = company.strip(), label.strip()
    if not c:
        return l
    if PLACE_LABEL.match(l):
        return c
    cn, ln = norm_name(c), norm_name(l)
    if not cn or not ln or cn in ln:
        return l
    return f"{c} — {l}"


def fetch(source: dict, cfg: dict, archive_dir: Path) -> list[Path]:
    paths, missing = [], []
    for p in source.get("pages") or []:
        if not p.get("url"):
            missing.append(p.get("company", "?")); continue
        slug = re.sub(r"[^a-z0-9]+", "-", p["company"].lower()).strip("-")
        page = http_get(p["url"], archive_dir / slug, "index.html")
        paths.append(page)
        follow = p.get("follow")
        if follow:
            html = page.read_text(encoding="utf-8", errors="replace")
            seen, n = set(), 0
            for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
                t = re.sub("<[^>]+>", " ", text)
                if follow.get("href") and follow["href"] not in href: continue
                if follow.get("text") and not re.search(follow["text"], t, re.I): continue
                url = urllib.parse.urljoin(p["url"], href)
                if url in seen or url.rstrip("/") == p["url"].rstrip("/"): continue
                seen.add(url); n += 1
                if n > MAX_FOLLOW: break
                paths.append(http_get(url, archive_dir / slug, re.sub(r"[^a-z0-9]+", "-", urllib.parse.urlparse(url).path.lower()).strip("-")[:80] + ".html"))
                time.sleep(0.5)
    (archive_dir / "pages.json").write_text(json.dumps({"fetched": [str(x) for x in paths], "no_url_on_record": missing}, indent=1))
    return paths


PRE_EXTRACTED_COLUMNS = ["company", "name", "address", "city", "state", "zip", "kind", "evidence", "source_url"]


def _from_csv(path: Path, source: dict) -> list[dict]:
    """Rows a person or another agent transcribed from the pages, instead of a model call per page.

    Extraction costs one call per archived page — 126 of them — which is the slowest and most
    failure-prone thing Layer 1 does, and it is the only reason this source needs AI at all. A
    locations.csv in the source's folder replaces all of it: the reading has already been done,
    the row carries the page it came from, and the run just loads it.

    The verbatim check that guards the model does not apply here and is not faked. Provenance is
    the source_url on each row, and every row is marked as transcribed so the warehouse can tell
    these apart from anything a fetcher parsed itself.
    """
    import csv as _csv
    rows = list(_csv.DictReader(open(path, newline="", encoding="utf-8-sig")))
    missing = [c for c in ("name", "city", "state") if rows and c not in rows[0]]
    require(not missing, path, f"pre-extracted CSV is missing columns {missing}; "
                               f"expected {PRE_EXTRACTED_COLUMNS}")
    out = []
    # Position is per file, so revising one company's CSV cannot shift the row numbers recorded
    # against every other company.
    for i, r in enumerate(rows, 1):
        name = (r.get("name") or "").strip()
        if not name:
            continue
        name = _qualify((r.get("company") or "").strip(), name)
        out.append(contract_row(source, i, name=name, address=(r.get("address") or "").strip(),
                                city=(r.get("city") or "").strip(), state=(r.get("state") or "").strip().upper(),
                                zip_code=(r.get("zip") or "").strip(),
                                source_url=(r.get("source_url") or "").strip() or (source.get("url") or ""),
                                source_document=path.name, status=(r.get("kind") or "").strip(),
                                notes=("transcribed from the company page, not model-extracted"
                                       + (f"; {r['evidence'].strip()}" if (r.get("evidence") or "").strip() else ""))[:200]))
    require(bool(out), path, "pre-extracted CSV produced no rows")
    return out



def parse(paths: list[Path], source: dict, cfg: dict | None = None) -> list[dict]:
    from ..registry import load_yaml
    cfg = cfg or load_yaml(ROOT / "registry" / "config.yaml")
    # Transcribed CSVs win outright: if the reading is already done, do not pay for it again.
    # One file per company, so a company whose page changed is re-transcribed and re-uploaded on
    # its own — the others keep their rows, their positions and the file they came from.
    # Transcribed CSVs win PER COMPANY, not for the whole source. The docstring has always said
    # "one file per company ... the others keep their rows", but the code returned as soon as any
    # CSV existed, so six carried-forward CSVs silently suppressed every HTML page — Banker Steel
    # and True House among them, which have no CSV and exist only as pages.
    pre = sorted((p for p in paths if p.suffix.lower() == ".csv"), key=lambda p: p.name)
    out: list[dict] = []
    audit: list[dict] = []
    pos = 0
    transcribed: set[str] = set()
    for f in pre:
        transcribed.add(f.stem.lower())
        out += _from_csv(f, source)
    for path in paths:
        if path.suffix.lower() == ".csv":
            continue
        if path.parent.name.lower() in transcribed:
            continue          # this company is already read by hand; do not pay for a model call

        slug = path.parent.name
        company = next((pg["company"] for pg in (source.get("pages") or [])
                        if re.sub(r"[^a-z0-9]+", "-", pg.get("company", "").lower()).strip("-") == slug),
                       slug.replace("-", " "))
        meta = json.loads((path.parent / f"{path.name}.meta.json").read_text()) if (path.parent / f"{path.name}.meta.json").exists() else {}
        url = meta.get("url", str(path))
        text = html_text(path.read_text(encoding="utf-8", errors="replace"))
        if len(text) < 200:
            audit.append({"page": url, "skipped": "page text under 200 chars (JS-rendered? blocked?)"}); continue
        res = extract_locations(text, company=company, page_url=url, cfg=cfg, prompt_path=PROMPT, archive_to=path.parent)
        audit.append({"page": url, "kept": len(res["locations"]), "dropped_not_verbatim": len(res["dropped"]), "model": res["model"], "prompt_hash": res["prompt_hash"]})
        for loc in res["locations"]:
            pos += 1
            out.append(contract_row(source, pos, name=_qualify(company, loc["name"]), address=loc["address"], city=loc["city"], state=loc["state"],
                                    zip_code=loc["zip"], source_url=url, source_document=path.name,
                                    status=loc.get("kind", ""), notes=(f"evidence: {loc['evidence']}" if loc.get("evidence") else "kind=unclear: page does not say this is a plant")[:200]))
    if audit:
        # The day folder, not paths[0]'s parent: a transcribed CSV sits at the folder root while a
        # page sits one level down, so keying off paths[0] wrote the audit to the wrong directory
        # as soon as a CSV sorted first.
        day = next((x.parent.parent for x in paths if x.suffix.lower() != ".csv"), paths[0].parent)
        (day / "extraction_audit.json").write_text(json.dumps(audit, indent=1))
    # de-duplicate a location that appears on both an index page and its own sub-page
    seen, dedup = set(), []
    for r in out:
        k = (r["name_verbatim"].lower(), r["address_verbatim"].lower(), r["city_verbatim"].lower())
        if k in seen: continue
        seen.add(k); dedup.append(r)
    return dedup


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source, cfg)
