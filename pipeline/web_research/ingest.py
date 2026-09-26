"""Web-research ingestion: an external agent's findings, validated, into the assertion table.

The agent researches a facility on the open web and inserts ONE JSON document per facility into
`web_research_submission`. It never writes a fact table. This job reads the pending documents,
validates every finding against the contract below (docs/web-research-agent.md), writes what passes
into fact_assertions and ref_source_row under the source `web_research`, and records the outcome on
the submission (status + report). The fact_assertions trigger then queues each facility, and the
15-minute golden refresh brings it into golden.

Provenance is per document:

  * every document the agent cites is a `sources[]` entry (url, title, found_by, retrieved_at, kind)
    and becomes a ref_source_row, plus one `research_source` assertion for the facility whose value
    is the URL: the source itself is on the record, with who found it;
  * every finding names the document it came from (`source_ref`) and the verbatim `quote` on that
    page that supports it, and becomes one fact_assertions row whose row_hash points at its own
    ref_source_row (source_url = the page, source_document = the quote, source_identifier = who
    found it). v_provenance then answers "where did this value come from" with a link and a quote.

A verdict of `not_ic` or `closed` (with at least one cited source) becomes existence_flag, basis
`web_verdict`, which takes the facility out of golden (golden.is_excluded). A person outranks it:
existence_flag = active from ADL_Viz feedback brings the plant back.

    python -m pipeline.web_research.ingest [--limit 200] [--dry-run]
"""
from __future__ import annotations
import hashlib, json, re, sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE = "web_research"
DEFAULT_CONFIDENCE = 0.6
KINDS = ("company_site", "government_registry", "certification_body", "trade_directory", "news",
         "map_listing", "social", "filing", "other")
VERDICTS = ("in_scope", "not_ic", "closed", "not_found", "duplicate")

# Source veracity: the most a document of this kind can vouch for. A finding's confidence is capped
# at its document's veracity, and only findings the document states literally can override.
VERACITY = {"government_registry": 0.8, "filing": 0.8, "certification_body": 0.8, "company_site": 0.7,
            "trade_directory": 0.6, "map_listing": 0.6, "news": 0.5, "social": 0.4, "other": 0.4}
INFERRED_CAP = 0.6            # a judgement from the page (category, material), not a value it states
# Fields a page states as a value that can be checked against the quote. A verified value from a
# document of veracity >= OVERRIDE_AT outranks every automated source (not a person): basis
# `web_verified` (0.8 documents) or `web_primary` (the company's own site). Anything else is
# `web_cited` or `web_inferred` and only fills a blank.
LITERAL = {"name", "legal_name", "address", "city", "state", "zip", "phone", "email", "website", "naics",
           "sq_ft", "building_sqft", "expiry_date", "lat_lon"}
OVERRIDE_AT = 0.7
OVERRIDING = ("web_verified", "web_primary")        # pipeline/golden.py ranks these above automation
REMOVAL_GRADE = ("government_registry", "filing", "certification_body")   # one of these can remove a plant alone

# Fields the agent may assert. Everything golden carries except what a person or the pipeline owns:
# existence_flag comes only from the verdict, adl_validated and employee_notes are ADL's own, and
# floor_area_sqft is derived from sq_ft and building_sqft by survivorship.
NOT_ASSERTABLE = {"existence_flag", "adl_validated", "employee_notes", "floor_area_sqft"}

_URL_FIELDS = {"website"}
_NUMERIC = {"sq_ft": (100, 50_000_000), "building_sqft": (100, 50_000_000),
            "annual_revenue_usd": (0, 1e12), "throughput": (0, 1e9), "utilisation_pct": (0, 100),
            "vacant_capacity": (0, 1e9)}


def assertable_fields() -> list[str]:
    from ..warehouse import GOLDEN_FIELDS
    return [f for f in GOLDEN_FIELDS if f not in NOT_ASSERTABLE]


def _taxonomy() -> tuple[set[str], set[str]]:
    from ..registry import load_yaml
    t = load_yaml(ROOT / "registry" / "taxonomy.yaml")
    groups = {g["name"] for g in t["groups"]}
    leaves = {l["name"] for g in t["groups"] for l in g.get("leaves", [])}
    return groups, leaves


def _is_url(v: str) -> bool:
    u = urlsplit(v.strip())
    return u.scheme in ("http", "https") and bool(u.hostname) and "." in (u.hostname or "")


def check_value(field: str, value: str, taxonomy: tuple[set[str], set[str]]) -> str | None:
    """None when the value is acceptable for the field, else why not."""
    from ..recovery.streets import US_STATE_CODES
    v = str(value).strip()
    if not v:
        return "empty value"
    if len(v) > 500:
        return "value longer than 500 characters"
    if field == "state" and v.lower() not in US_STATE_CODES:
        return "state must be a two-letter US state code"
    if field == "zip" and not re.fullmatch(r"\d{5}(-\d{4})?", v):
        return "zip must be 5 digits or ZIP+4"
    if field == "lat_lon":
        m = re.fullmatch(r"\s*(-?\d+(\.\d+)?)\s*,\s*(-?\d+(\.\d+)?)\s*", v)
        if not m or not (-90 <= float(m.group(1)) <= 90 and -180 <= float(m.group(3)) <= 180):
            return "lat_lon must be 'lat,lon' in decimal degrees"
    if field in _URL_FIELDS and not _is_url(v):
        return "must be an http(s) URL"
    if field == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", v):
        return "not an email address"
    if field == "phone" and len(re.sub(r"\D", "", v)) < 10:
        return "phone needs at least 10 digits"
    if field in _NUMERIC:
        try:
            n = float(v.replace(",", ""))
        except ValueError:
            return "must be a number"
        lo, hi = _NUMERIC[field]
        if not lo <= n <= hi:
            return f"out of range {lo}..{hi}"
    if field == "capability_group" and v not in taxonomy[0]:
        return "not a group in registry/taxonomy.yaml"
    if field == "capability_leaf" and v not in taxonomy[1]:
        return "not a leaf in registry/taxonomy.yaml"
    return None


def _alnum(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v).lower())


def _host(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").lower().removeprefix("www.")


def homepage(url: str) -> str:
    """A website value is the site, not a page on it: https://example.com/about -> https://example.com"""
    u = urlsplit(url.strip())
    return f"{u.scheme}://{u.hostname.lower()}" if u.hostname else url


def stated(field: str, value: str, quote: str, source_url: str) -> bool:
    """Does the quote (or, for a website, the document itself) literally state this value?"""
    from ..recovery.streets import street_equivalent
    from ..contract import _US_NAMES
    q = str(quote)
    if field == "website":
        return _host(value) == _host(source_url) or _host(value) in q.lower()
    if field == "phone":
        return re.sub(r"\D", "", value)[-10:] in re.sub(r"\D", "", q)
    if field in ("zip",):
        return value[:5] in re.findall(r"\d{5}", q)
    if field == "state":
        return (re.search(rf"\b{re.escape(value)}\b", q, re.I) is not None
                or any(n in q.lower() and c == value.upper() for n, c in _US_NAMES.items()))
    if field == "address":
        num = re.match(r"\s*(\d+)", value)
        if not num:
            return _alnum(value) in _alnum(q)
        for m in re.finditer(rf"\b{num.group(1)}\b[^,\n]*", q):
            if street_equivalent(value, m.group(0))[0] or _alnum(value) in _alnum(m.group(0)):
                return True
        return False
    if field in ("sq_ft", "building_sqft"):
        return re.sub(r"\D", "", value.split(".")[0]) in re.sub(r"\D", "", q)
    return _alnum(value) in _alnum(q)


def _h(*parts) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


def plan(payload: dict, facility_id: str, active: set[str], fields: set[str], taxonomy) -> dict:
    """Validate one submission. Pure: returns the rows to write and a report of what was refused."""
    rejected: list[dict] = []
    if facility_id not in active:
        return {"sources": [], "facts": [], "rejected": [{"item": "submission",
                "reason": f"{facility_id} is not an active registered facility"}], "fatal": True}
    if str(payload.get("facility_id") or facility_id) != facility_id:
        return {"sources": [], "facts": [], "rejected": [{"item": "submission",
                "reason": "payload facility_id does not match the submission row"}], "fatal": True}
    found_by_default = str(payload.get("agent") or "").strip()
    sources: dict[str, dict] = {}
    for i, s in enumerate(payload.get("sources") or []):
        ref, url = str(s.get("source_ref") or "").strip(), str(s.get("url") or "").strip()
        why = ("missing source_ref" if not ref else "duplicate source_ref" if ref in sources
               else "url must be an http(s) link" if not _is_url(url)
               else "found_by is required" if not (s.get("found_by") or found_by_default) else None)
        if why:
            rejected.append({"item": f"sources[{i}]", "reason": why}); continue
        kind = s.get("kind") if s.get("kind") in KINDS else "other"
        when = str(s.get("retrieved_at") or date.today().isoformat())[:10]
        sources[ref] = {"ref": ref, "url": url, "title": str(s.get("title") or "")[:300], "kind": kind,
                        "found_by": str(s.get("found_by") or found_by_default)[:200], "retrieved": when,
                        "row_hash": _h(SOURCE, facility_id, url)}
    facts: list[dict] = []
    for i, a in enumerate(payload.get("assertions") or []):
        field, value = str(a.get("field") or "").strip(), str(a.get("value") or "").strip()
        src, quote = sources.get(str(a.get("source_ref") or "")), str(a.get("quote") or "").strip()
        conf = a.get("confidence", DEFAULT_CONFIDENCE)
        why = ("field is not assertable" if field not in fields
               else "source_ref names no valid source" if not src
               else "a verbatim quote from the source is required" if len(quote) < 3
               else "confidence must be between 0 and 1" if not isinstance(conf, (int, float)) or not 0 <= conf <= 1
               else check_value(field, value, taxonomy))
        if not why and field == "website":
            value = homepage(value)
        literal = field in LITERAL
        if not why and literal and not stated(field, value, quote, src["url"]):
            why = "the quote does not state this value (copy the words that contain it)"
        if why:
            rejected.append({"item": f"assertions[{i}]", "field": field, "reason": why}); continue
        veracity = VERACITY[src["kind"]]
        conf = min(float(conf), veracity if literal else min(veracity, INFERRED_CAP))
        basis = ("web_inferred" if not literal
                 else "web_verified" if veracity >= 0.8 and conf >= OVERRIDE_AT
                 else "web_primary" if veracity >= OVERRIDE_AT and conf >= OVERRIDE_AT
                 else "web_cited")
        facts.append({"field": field, "value": value, "basis": basis, "confidence": conf,
                      "source": src, "quote": quote[:1000]})
    verdict = payload.get("verdict") or {}
    status = verdict.get("status")
    if status and status not in VERDICTS:
        rejected.append({"item": "verdict", "reason": f"status must be one of {VERDICTS}"})
    elif status in ("not_ic", "closed"):
        cited = [sources[r] for r in verdict.get("source_refs") or [] if r in sources]
        reason = str(verdict.get("reason") or "").strip()
        strong = [c for c in cited if c["kind"] in REMOVAL_GRADE]
        if not cited or len(reason) < 10:
            rejected.append({"item": "verdict", "reason": "not_ic / closed needs a reason and at least one cited source"})
        elif len({c["url"] for c in cited}) < 2 and not strong:
            # Taking a plant out of golden on one directory or map listing proved too easy: 160+
            # removals in one batch, each on a single citation. Two independent pages, or one
            # registry / filing / certification body, are required; the facility stays meanwhile.
            rejected.append({"item": "verdict", "reason": "not_ic / closed needs two cited sources (different pages) "
                             "or one government_registry, filing or certification_body source"})
        else:
            best = max(cited, key=lambda x: VERACITY[x["kind"]])
            facts.append({"field": "existence_flag", "value": status, "basis": "web_verdict",
                          "confidence": min(float(verdict.get("confidence", DEFAULT_CONFIDENCE)), VERACITY[best["kind"]]),
                          "source": best, "quote": reason[:1000]})
    duplicate = None
    if status == "duplicate":
        other = str(verdict.get("duplicate_of") or "").strip()
        cited = [sources[r] for r in verdict.get("source_refs") or [] if r in sources]
        reason = str(verdict.get("reason") or "").strip()
        why = ("duplicate_of must name another active facility" if other == facility_id or other not in active
               else "duplicate needs a reason and at least one cited source" if not cited or len(reason) < 10 else None)
        if why:
            rejected.append({"item": "verdict", "reason": why})
        else:
            duplicate = {"duplicate_of": other, "reason": reason[:1000], "urls": [c["url"] for c in cited]}
    # A submission must show its research: with no valid source at all there is nothing to record,
    # so it is refused and the facility goes back into the queue.
    if not sources:
        rejected.append({"item": "sources", "reason": "no sources cited: cite at least the pages you checked, "
                         "even for not_found"})
    # One research_source assertion per document actually used, so each source is itself on record.
    # A not_found verdict records the pages it checked, so the search itself is on file.
    used = {f["source"]["ref"] for f in facts}
    if status == "not_found":
        used |= set(sources)
    for ref in sorted(used):
        s = sources[ref]
        facts.append({"field": "research_source", "value": s["url"], "basis": f"source:{s['kind']}",
                      "confidence": None, "source": s, "quote": s["title"] or s["url"], "is_source_tag": True})
    return {"sources": [sources[r] for r in sorted(used)], "facts": facts, "rejected": rejected, "fatal": False,
            "duplicate": duplicate}


def _rows(facility_id: str, p: dict, release_tag: str, now: str) -> tuple[list[tuple], list[tuple]]:
    """ref_source_row and fact_assertions tuples. Each fact gets its own ref row (its quote); each
    document's research_source tag shares the document's row."""
    refs, facts = {}, []
    for f in p["facts"]:
        s = f["source"]
        if f.get("is_source_tag"):
            rh = s["row_hash"]
            refs[rh] = (rh, SOURCE, s["url"], s["title"] or s["url"], s["retrieved"], s["kind"], s["found_by"],
                        None, None, None, None, None, facility_id, "web_research", None, release_tag)
        else:
            rh = _h(SOURCE, facility_id, s["url"], f["field"], f["value"])
            refs[rh] = (rh, SOURCE, s["url"], f["quote"], s["retrieved"], f["field"], s["found_by"],
                        None, None, None, None, None, facility_id, "web_research", f["confidence"], release_tag)
        aid = _h(SOURCE, "assertion", facility_id, f["field"], f["value"], s["url"])
        facts.append((aid, release_tag, facility_id, SOURCE, f["field"], s["retrieved"], f["value"],
                      f["basis"], 0, rh, f["confidence"], SOURCE, now))
    return list(refs.values()), facts


def ingest(wh, *, limit: int = 200, dry_run: bool = False) -> dict:
    from ..golden_refresh import current_release
    from ..warehouse import SYNTHETIC_SOURCES, _date_row
    tag = current_release(wh)
    active = {r["facility_id"] for r in wh.query("SELECT facility_id FROM facility WHERE status = 'active'")}
    fields, taxonomy = set(assertable_fields()), _taxonomy()
    pending = wh.query("SELECT submission_id, facility_id, payload FROM web_research_submission "
                       "WHERE status = 'pending' ORDER BY submitted_at LIMIT ?", (limit,))
    now = datetime.now(timezone.utc).isoformat()
    totals = {"submissions": len(pending), "ingested": 0, "partial": 0, "rejected": 0,
              "facts_written": 0, "sources_written": 0, "exclusions": 0, "duplicates": 0, "overrides": 0,
              "dry_run": dry_run}
    outcomes = []
    for sub in pending:
        try:
            payload = json.loads(sub["payload"])
            p = plan(payload, sub["facility_id"], active, fields, taxonomy)
        except (ValueError, TypeError, AttributeError) as e:
            p = {"sources": [], "facts": [], "rejected": [{"item": "payload", "reason": f"unreadable: {e}"}], "fatal": True}
        real = [f for f in p["facts"] if not f.get("is_source_tag")]
        dup = p.get("duplicate")
        status = ("rejected" if p["fatal"] or (not real and not dup and p["rejected"])
                  else "partial" if p["rejected"] else "ingested")
        report = {"facts": len(real), "sources": len(p["sources"]),
                  "overrides": sum(1 for f in real if f["basis"] in OVERRIDING),
                  "exclusion": next((f["value"] for f in real if f["field"] == "existence_flag"), None),
                  "duplicate_of": dup["duplicate_of"] if dup else None,
                  "rejected": p["rejected"]}
        totals[status] += 1
        totals["exclusions"] += 1 if report["exclusion"] else 0
        totals["duplicates"] += 1 if report["duplicate_of"] else 0
        totals["overrides"] += report["overrides"]
        outcomes.append({"submission_id": sub["submission_id"], "facility_id": sub["facility_id"], "status": status, **report})
        if dry_run:
            continue
        refs, facts = _rows(sub["facility_id"], p, tag, now) if status != "rejected" else ([], [])
        with wh.transaction() as c:
            if facts:
                c.executemany("INSERT INTO dim_source VALUES (?,?,?,?,?,?,?) ON CONFLICT (source_key) DO NOTHING",
                              [(SOURCE, SOURCE, SYNTHETIC_SOURCES[SOURCE]["name"], SOURCE, "web_research_agent", None, "active")])
                c.executemany("INSERT INTO dim_date VALUES (?,?,?,?) ON CONFLICT (date_key) DO NOTHING",
                              [d for d in {_date_row(f[5]) for f in facts} if d])
                c.executemany("INSERT INTO ref_source_row VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                              "ON CONFLICT (row_hash) DO NOTHING", refs)
                c.executemany("INSERT INTO fact_assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                              "ON CONFLICT (assertion_id, release_tag) DO NOTHING", facts)
            if dup and status != "rejected":
                # A proposed merge, never an automatic one: it waits in the duplicate queue (#52).
                c.execute("INSERT INTO facility_duplicate_candidate (facility_id, duplicate_of, source, tier, evidence, "
                          "created_at) VALUES (?, ?, ?, 'likely', ?, ?) ON CONFLICT (facility_id, duplicate_of) DO NOTHING",
                          (sub["facility_id"], dup["duplicate_of"], SOURCE, json.dumps(dup), now))
            c.execute("UPDATE web_research_submission SET status = ?, processed_at = ?, report = ? WHERE submission_id = ?",
                      (status, now, json.dumps(report), sub["submission_id"]))
        totals["facts_written"] += sum(1 for f in facts if f[4] != "research_source")
        totals["sources_written"] += sum(1 for f in facts if f[4] == "research_source")
    totals["outcomes"] = outcomes[:50]
    return totals


def main(argv=None) -> int:
    import argparse
    from ..registry import load_yaml
    from ..warehouse import open_warehouse, SqliteWarehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.web_research.ingest")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    ap.add_argument("--limit", type=int, default=200, help="submissions per run")
    ap.add_argument("--dry-run", action="store_true", help="validate and report; write nothing")
    ap.add_argument("--fields", action="store_true", help="print the assertable fields and exit")
    args = ap.parse_args(argv)
    if args.fields:
        print("\n".join(assertable_fields())); return 0
    wh = (SqliteWarehouse(Path(args.db)) if args.db
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    print(json.dumps(ingest(wh, limit=args.limit, dry_run=args.dry_run), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
