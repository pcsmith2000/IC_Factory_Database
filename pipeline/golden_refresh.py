"""Continuous golden: recompute only the facilities whose assertions changed (#44, epic #39).

Golden used to change only when a whole pipeline ran: layers 1-8 replaced it, and enrichment's
promote stage replaced it again. An assertion written in between (a web lookup, an employee
correction, a Tako result) reached nobody until the next full run. Now:

  golden_dirty      a queue of (facility_key, release_tag) pairs. A statement trigger on
                    fact_assertions fills it (pipeline/warehouse.py), and so do facility merges.
  refresh           drains the queue in batches. Each pair resolves to its permanent facility
                    through the registry (#41-#43). For each facility it reads the golden basis,
                    applies the same golden.build_golden rules and the E10 state gate that
                    promote applies, and upserts or removes that one golden row.

The golden basis of a permanent facility is what promote reads: the current release's
assertions, plus carried enrichment, Tako, ASTRA and employee-feedback assertions from any
release. The difference is that carried facts now reach their plant through the permanent
registry (v_assertions_resolved), not through E11's name-and-address carry.

Safety:
  * Only registered facilities are written or removed. A key that doesn't resolve (an ADL-*
    facility an employee created in ADL_Viz, a retired number) is left exactly as it is and
    reported.
  * Per-facility loss: a facility that would lose a field it now carries is not written. It stays
    in the queue and is reported, unless the field was withheld by E10 or the facility was ruled
    not IC. This is E6 applied one facility at a time.
  * A full refresh (--all) and a sequence of incremental refreshes give the same table (tested).

    python -m pipeline.golden_refresh [--all] [--batch 500] [--max-batches N] [--dry-run]
    python -m pipeline.golden_refresh --snapshot       # freeze golden into golden_release (#49)
"""
from __future__ import annotations
import json, sys
from datetime import datetime, timezone
from pathlib import Path

from . import golden as golden_mod
from .enrich import geo
from .enrich.promote import build
from .registry import load_yaml
from .warehouse import GOLDEN_FIELDS

ROOT = Path(__file__).resolve().parent.parent
CARRIED_CLASSES = ("enrichment", "tako_ai_search", "astra_manual_web_lookup", "human_feedback")

# Each queued (facility_key, release_tag) resolves to a permanent facility the way
# v_assertions_resolved does: through its release's registry and legacy_id_map, or directly when
# the release predates no registry (a release loaded through the permanent registry, #43, or a
# merge's own queue entry). One merged_into hop, which a merge guarantees is enough.
RESOLVE_QUEUE = """
    SELECT d.facility_key, d.release_tag,
           COALESCE(fm.merged_into, m.facility_id, fd.merged_into, fd.facility_id) AS facility_id
    FROM golden_dirty d
    LEFT JOIN release_registry rr ON rr.release_tag = d.release_tag
    LEFT JOIN legacy_id_map m ON m.registry_hash = rr.registry_hash AND m.legacy_id = d.facility_key
    LEFT JOIN facility fm ON fm.facility_id = m.facility_id
    LEFT JOIN facility fd ON rr.release_tag IS NULL AND fd.facility_id = d.facility_key"""


def _basis_sql(n: int) -> str:
    marks = ",".join("?" * n)
    classes = ",".join(f"'{c}'" for c in CARRIED_CLASSES)
    return f"""
    SELECT permanent_facility_id AS facility_id, source_key AS source_id, release_tag,
           COALESCE(source_class, '') AS source_class, COALESCE(date_key, '') AS retrieved_date,
           COALESCE(row_hash, '') AS row_hash, COALESCE(basis, 'none') AS basis,
           COALESCE(site_visit, 0) AS site_visit, COALESCE(confidence, 0) AS confidence,
           COALESCE(asserted_at, '') AS asserted_at, field_key AS field, value
    FROM v_assertions_resolved
    WHERE permanent_facility_id IN ({marks})
      AND (release_tag = ? OR source_class IN ({classes}))
    ORDER BY permanent_facility_id, field_key, assertion_id"""


def current_release(wh) -> str:
    tags = [r["release_tag"] for r in wh.query("SELECT DISTINCT release_tag FROM golden_facility")]
    if len(tags) != 1:
        raise RuntimeError(f"golden_facility must hold exactly one release, found {len(tags)}")
    return tags[0]


def compute_full(wh, facility_ids: list[str], release_tag: str, rules: dict) -> dict:
    """Golden for these facilities, as data: rows (after E10 and the not-IC split), unfiltered rows
    (before E10, which promote's E6 measures against), E10's withheld coordinates, the facilities
    ruled not IC, and conflicts. Reads the database, writes nothing.

    A facility is in golden only while the current release asserts something about it. Carried
    facts (enrichment, Tako, ASTRA, employee feedback) keep a plant's paid-for detail, but they must
    not resurrect a plant a later release dropped: that is the scope promote's EXISTS clause kept
    before the registry, and it is kept here."""
    empty = {"rows": {}, "unfiltered": [], "quarantined": [], "excluded": set(), "conflicts": []}
    if not facility_ids:
        return empty
    asserts = wh.query(_basis_sql(len(facility_ids)), (*facility_ids, release_tag))
    present = {a["facility_id"] for a in asserts if a["release_tag"] == release_tag}
    asserts = [a for a in asserts if a["facility_id"] in present]
    if not asserts:
        return empty
    for a in asserts:
        a["source_class"] = a["source_class"] or "?"     # golden.py's unknown-class sentinel
    rows, conflicts = build(asserts, rules)
    unfiltered = [dict(r) for r in rows]
    quarantined = []
    for _ in range(5):                                   # E10, exactly as promote applies it
        bad = geo.out_of_state(rows)
        if not bad:
            break
        quarantined.extend(bad)
        withheld = {(b["facility_id"], b["lat_lon"]) for b in bad}
        asserts = [a for a in asserts if not (a["field"] == "lat_lon" and (a["facility_id"], a["value"]) in withheld)]
        rows, conflicts = build(asserts, rules)
    rows, conflicts, excluded = golden_mod.split_excluded(rows, conflicts)
    return {"rows": {r["facility_id"]: r for r in rows}, "unfiltered": unfiltered, "quarantined": quarantined,
            "excluded": {g["facility_id"] for g in excluded}, "conflicts": conflicts}


def compute(wh, facility_ids: list[str], release_tag: str, rules: dict) -> tuple[dict, list[dict], set[str]]:
    """(golden row per facility, coordinates E10 withheld, facilities ruled not IC)."""
    out = compute_full(wh, facility_ids, release_tag, rules)
    return out["rows"], out["quarantined"], out["excluded"]


def active_facilities(wh) -> list[str]:
    return sorted(r["facility_id"] for r in wh.query("SELECT facility_id FROM facility WHERE status = 'active'"))


def snapshot(wh, release_tag: str | None = None) -> dict:
    """Freeze golden as it stands into golden_release under its release tag (#49). Golden is live now,
    so a release is this copy: what the release said, kept after golden moves on. Re-snapshotting a
    tag replaces that tag's copy. Rows are stored as JSON so a golden column added later needs no
    migration here."""
    tag = release_tag or current_release(wh)
    rows = wh.query("SELECT * FROM golden_facility")
    at = datetime.now(timezone.utc).isoformat()
    with wh.transaction() as c:
        c.execute("DELETE FROM golden_release WHERE release_tag = ?", (tag,))
        c.executemany("INSERT INTO golden_release (release_tag, facility_key, snapshot_at, row_json) VALUES (?, ?, ?, ?)",
                      [(tag, r["facility_key"], at, json.dumps(r, default=str, sort_keys=True)) for r in rows])
    return {"release_tag": tag, "rows": len(rows), "snapshot_at": at}


def _columns() -> list[str]:
    return ["facility_key", "release_tag"] + [x for f in GOLDEN_FIELDS for x in (f, f"{f}__source")] + \
           ["n_assertions", "n_sources"]


def _text(v):
    return None if v in (None, "") else str(v)


def _losses(before: dict | None, after: dict | None, withheld_ll: bool, excluded: bool) -> list[str]:
    if before is None or excluded:
        return []
    return [f for f in GOLDEN_FIELDS
            if before.get(f) not in (None, "") and (after is None or after.get(f) in (None, ""))
            and not (f == "lat_lon" and withheld_ll)]


def refresh(wh, *, all_facilities: bool = False, batch: int = 500, max_batches: int | None = None,
            dry_run: bool = False, rules: dict | None = None, release_tag: str | None = None) -> dict:
    rules = rules or load_yaml(ROOT / "registry" / "survivorship.yaml")
    tag = release_tag or current_release(wh)
    if all_facilities:
        # Every registered live facility, plus whatever golden holds that the registry knows.
        work = {f: [] for f in active_facilities(wh)}
        queued = []
    else:
        queued = wh.query(RESOLVE_QUEUE)
        work: dict[str, list[tuple[str, str]]] = {}
        for q in queued:
            if q["facility_id"]:
                work.setdefault(q["facility_id"], []).append((q["facility_key"], q["release_tag"]))
    unresolved = [(q["facility_key"], q["release_tag"]) for q in queued if not q["facility_id"]]
    report = {"release_tag": tag, "queued_pairs": len(queued), "facilities": len(work),
              "unresolved_pairs": len(unresolved), "written": 0, "removed": 0, "unchanged": 0,
              "held_for_loss": [], "coordinates_withheld": 0, "batches": 0, "dry_run": dry_run,
              "changed_fields": {}}
    ids = sorted(work)
    cols = _columns()
    for i in range(0, len(ids), batch):
        if max_batches is not None and report["batches"] >= max_batches:
            break
        chunk = ids[i:i + batch]
        report["batches"] += 1
        new, quarantined, excluded = compute(wh, chunk, tag, rules)
        report["coordinates_withheld"] += len(quarantined)
        withheld_ll = {q["facility_id"] for q in quarantined}
        marks = ",".join("?" * len(chunk))
        old = {r["facility_key"]: r for r in wh.query(
            f"SELECT * FROM golden_facility WHERE facility_key IN ({marks})", tuple(chunk))}
        writes, removes, done = [], [], []
        for fid in chunk:
            before, after = old.get(fid), new.get(fid)
            lost = _losses(before, after, fid in withheld_ll, fid in excluded)
            if lost:
                report["held_for_loss"].append({"facility_id": fid, "fields": lost})
                continue                                   # stays queued; a person decides
            done.append(fid)
            if after is None:
                if before is not None:
                    removes.append(fid)
                continue
            vals = [fid, tag] + [_text(after.get(x)) for f in GOLDEN_FIELDS for x in (f, f"{f}__source")] + \
                   [after.get("n_assertions"), after.get("n_sources")]
            if before is not None and all(_text(before.get(c)) == _text(v) for c, v in zip(cols, vals)
                                          if c not in ("n_assertions", "n_sources")):
                report["unchanged"] += 1
                continue
            for f in GOLDEN_FIELDS:
                if _text((before or {}).get(f)) != _text(after.get(f)):
                    report["changed_fields"][f] = report["changed_fields"].get(f, 0) + 1
            writes.append(vals)
        report["written"] += len(writes)
        report["removed"] += len(removes)
        if dry_run:
            continue
        with wh.transaction() as c:
            gone = [w[0] for w in writes] + removes
            if gone:
                c.execute(f"DELETE FROM golden_facility WHERE facility_key IN ({','.join('?' * len(gone))})", tuple(gone))
            if writes:
                quoted = ", ".join(f'"{x}"' for x in cols)
                c.executemany(f"INSERT INTO golden_facility ({quoted}) VALUES ({','.join('?' * len(cols))})", writes)
            pairs = [p for fid in done for p in work.get(fid, [])]
            if pairs:
                c.executemany("DELETE FROM golden_dirty WHERE facility_key = ? AND release_tag = ?", pairs)
    # Golden holds live plants only: a number merged into another or retired leaves it. (Its facts
    # already resolve to the merge root above; this removes the row still filed under the old number.)
    not_live = [r["facility_key"] for r in wh.query(
        "SELECT g.facility_key FROM golden_facility g JOIN facility f ON f.facility_id = g.facility_key "
        "WHERE f.status <> 'active'")]
    report["removed_not_live"] = len(not_live)
    if not dry_run and not_live:
        with wh.transaction() as c:
            c.execute(f"DELETE FROM golden_facility WHERE facility_key IN ({','.join('?' * len(not_live))})", tuple(not_live))
    if not dry_run and unresolved and (max_batches is None or report["batches"] * batch >= len(ids)):
        # Nothing to do for a key the registry cannot resolve; it is reported, not retried forever.
        with wh.transaction() as c:
            c.executemany("DELETE FROM golden_dirty WHERE facility_key = ? AND release_tag = ?", unresolved)
    report["unresolved_sample"] = unresolved[:10]
    report["held_for_loss"] = report["held_for_loss"][:50]
    return report


def main(argv=None) -> int:
    import argparse
    from .warehouse import open_warehouse, SqliteWarehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.golden_refresh")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    ap.add_argument("--all", action="store_true", help="every registered facility, not just the queue")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--max-batches", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="compute and report; write nothing, drain nothing")
    ap.add_argument("--snapshot", action="store_true", help="freeze golden as it stands into golden_release; no refresh")
    args = ap.parse_args(argv)
    wh = (SqliteWarehouse(Path(args.db)) if args.db
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    try:
        rep = (snapshot(wh) if args.snapshot else
               refresh(wh, all_facilities=args.all, batch=args.batch, max_batches=args.max_batches, dry_run=args.dry_run))
    except RuntimeError as e:
        print(f"refresh refused, nothing written: {e}", file=sys.stderr); return 2
    print(json.dumps(rep, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
