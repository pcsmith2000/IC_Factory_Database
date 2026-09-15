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

ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT = ROOT / "prompts" / "EXTRACTION-PROMPT.md"
MAX_FOLLOW = 150


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


def parse(paths: list[Path], source: dict, cfg: dict | None = None) -> list[dict]:
    from ..registry import load_yaml
    cfg = cfg or load_yaml(ROOT / "registry" / "config.yaml")
    out, audit, pos = [], [], 0
    for path in paths:
        company = path.parent.name.replace("-", " ")
        meta = json.loads((path.parent / f"{path.name}.meta.json").read_text()) if (path.parent / f"{path.name}.meta.json").exists() else {}
        url = meta.get("url", str(path))
        text = html_text(path.read_text(encoding="utf-8", errors="replace"))
        if len(text) < 200:
            audit.append({"page": url, "skipped": "page text under 200 chars (JS-rendered? blocked?)"}); continue
        res = extract_locations(text, company=company, page_url=url, cfg=cfg, prompt_path=PROMPT, archive_to=path.parent)
        audit.append({"page": url, "kept": len(res["locations"]), "dropped_not_verbatim": len(res["dropped"]), "model": res["model"], "prompt_hash": res["prompt_hash"]})
        for loc in res["locations"]:
            pos += 1
            out.append(contract_row(source, pos, name=loc["name"], address=loc["address"], city=loc["city"], state=loc["state"],
                                    zip_code=loc["zip"], source_url=url, source_document=path.name,
                                    status=loc.get("kind", ""), notes=(f"evidence: {loc['evidence']}" if loc.get("evidence") else "kind=unclear: page does not say this is a plant")[:200]))
    if paths:
        (paths[0].parent.parent / "extraction_audit.json").write_text(json.dumps(audit, indent=1))
    # de-duplicate a location that appears on both an index page and its own sub-page
    seen, dedup = set(), []
    for r in out:
        k = (r["name_verbatim"].lower(), r["address_verbatim"].lower(), r["city_verbatim"].lower())
        if k in seen: continue
        seen.add(k); dedup.append(r)
    return dedup


def pull(source: dict, cfg: dict, archive_dir: Path) -> list[dict]:
    return parse(fetch(source, cfg, archive_dir), source, cfg)
