"""The permanent facility registry: one IC-number per plant, minted once, never reissued.

Epic #39. Until now an IC-number came from id_registry.json, a file in git keyed by the
reconcile signature (pipeline/reconcile.py). Every pipeline run minted into its own copy and
committed it back on its own branch, so forks of one base handed the same next numbers to
different plants: the warehouse holds 21 releases built from 17 diverging registries, and 2,286
IC-numbers name a facility in a different state in another release. The number also followed the
signature, not the plant, so a respelled name or a newly found address meant a new number.

Here the registry is four tables in the warehouse (DDL in pipeline/warehouse.py):

    facility            the plant: IC-number, status active | merged | retired, merged_into
    facility_match_key  signature -> facility; a plant has many keys, a key names one plant
    facility_event      every mint, seed, merge, split, retire and re-point, with actor and reason
    legacy_id_map       (registry hash, historical IC-number) -> permanent facility (#42)

and the counter is a Postgres sequence (a one-row table on SQLite) that starts above every number
ever issued. This module seeds the registry from the current golden table, whose numbers the owner
chose to keep as the permanent ones (2026-09-24), and reports its state.

    python -m pipeline.facility_registry seed [--dry-run]
    python -m pipeline.facility_registry status
"""
from __future__ import annotations
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Above every IC-number any registry ever issued: main's id_registry.json stops at IC-96820, the
# release branches at IC-96839 (release/35412004405), and fact_assertions holds nothing higher.
# No number at or below this is ever minted again, so no historical reference can be revived.
ID_FLOOR = 96841

# The confidence reconcile attaches to each signature kind (pipeline/reconcile.run).
KEY_METHODS = {"E": ("entity+street", 0.95), "S": ("street_key", 0.90),
               "N": ("name+city", 0.70), "NFP": ("no-fixed-plant", 0.60)}


class SeedRefused(RuntimeError):
    """The seed would contradict the registry already there, or cannot cover golden. Nothing written."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key_method(match_key: str) -> tuple[str, float]:
    kind = match_key.split("|", 2)[0] if match_key.startswith("NFP|") else match_key.split("|", 2)[1]
    return KEY_METHODS.get(kind, ("unknown", None))


def event_id(kind: str, facility_id: str, match_key: str = "") -> str:
    """Deterministic for a seed, so re-running it adds nothing."""
    return hashlib.sha256(f"{kind}\x1f{facility_id}\x1f{match_key}".encode()).hexdigest()[:20]


def id_number(facility_id: str) -> int | None:
    try:
        return int(facility_id.split("-", 1)[1]) if facility_id.startswith("IC-") else None
    except ValueError:
        return None


def plan_seed(golden_ids: list[str], registry: dict, existing_keys: dict[str, str]) -> dict:
    """What the seed would write, as data. Pure, so a test can hold it to its rules.

    Every golden facility keeps its number and gets the signature(s) main's registry holds for it.
    Refused outright when a golden id has no signature (the registry and golden disagree about
    what exists) or when a signature is already a key of a different facility (the seed would
    re-point a plant without an event). A partial seed would be worse than none.
    """
    by_id: dict[str, list[str]] = {}
    for sig, fid in registry.get("ids", {}).items():
        by_id.setdefault(fid, []).append(sig)
    golden = sorted(set(golden_ids))
    missing = [f for f in golden if f not in by_id]
    keys = [(sig, fid) for fid in golden for sig in sorted(by_id.get(fid, []))]
    collisions = [(sig, existing_keys[sig], fid) for sig, fid in keys
                  if sig in existing_keys and existing_keys[sig] != fid]
    numbers = [n for n in (id_number(f) for f in registry.get("ids", {}).values()) if n is not None]
    floor = max([ID_FLOOR, int(registry.get("next") or 0)] + [n + 1 for n in numbers])
    return {"facilities": golden, "keys": keys, "missing": missing, "collisions": collisions,
            "floor": floor}


def _counter_next(wh, c) -> int:
    if wh.engine == "postgres":
        r = wh._rows(c.execute("SELECT last_value, is_called FROM facility_id_seq"))[0]
        return int(r["last_value"]) + (1 if r["is_called"] else 0)
    return int(wh._rows(c.execute("SELECT next FROM facility_id_counter WHERE name = 'facility_id'"))[0]["next"])


def _raise_counter(wh, c, floor: int) -> int:
    """Never lowers: a counter already past the floor stays where it is."""
    target = max(_counter_next(wh, c), floor)
    if wh.engine == "postgres":
        c.execute("SELECT setval('facility_id_seq', ?, false)", (target,))
    else:
        c.execute("UPDATE facility_id_counter SET next = ? WHERE name = 'facility_id'", (target,))
    return target


def seed(wh, registry_path: Path | None = None, actor: str = "facility_registry.seed",
         dry_run: bool = False) -> dict:
    registry = json.loads((registry_path or ROOT / "id_registry.json").read_text())
    with wh.transaction() as c:
        golden = [r["facility_key"] for r in wh._rows(c.execute("SELECT facility_key FROM golden_facility"))]
        existing = {r["match_key"]: r["facility_id"] for r in
                    wh._rows(c.execute("SELECT match_key, facility_id FROM facility_match_key"))}
        before_f = wh._rows(c.execute("SELECT COUNT(*) AS n FROM facility"))[0]["n"]
    plan = plan_seed(golden, registry, existing)
    report = {"golden": len(plan["facilities"]), "keys_planned": len(plan["keys"]),
              "missing_from_registry": plan["missing"][:20], "n_missing": len(plan["missing"]),
              "collisions": plan["collisions"][:20], "n_collisions": len(plan["collisions"]),
              "facilities_before": int(before_f), "dry_run": dry_run}
    if not plan["facilities"]:
        raise SeedRefused("golden_facility is empty: nothing to seed from")
    if plan["missing"] or plan["collisions"]:
        raise SeedRefused(json.dumps(report, indent=1))
    if dry_run:
        return report
    at = _now()
    with wh.transaction() as c:
        c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                      "VALUES (?, 'active', NULL, ?, ?) ON CONFLICT (facility_id) DO NOTHING",
                      [(f, at, actor) for f in plan["facilities"]])
        c.executemany("INSERT INTO facility_match_key (match_key, facility_id, method, confidence, source, first_seen) "
                      "VALUES (?, ?, ?, ?, 'id_registry.json@main', ?) ON CONFLICT (match_key) DO NOTHING",
                      [(sig, fid, *key_method(sig), at) for sig, fid in plan["keys"]])
        c.executemany("INSERT INTO facility_event (event_id, at, kind, facility_id, other_facility_id, match_key, actor, reason) "
                      "VALUES (?, ?, 'seed', ?, NULL, NULL, ?, ?) ON CONFLICT (event_id) DO NOTHING",
                      [(event_id("seed", f), at, f, actor, "permanent id kept from golden_facility (#41)")
                       for f in plan["facilities"]])
        report["counter_next"] = _raise_counter(wh, c, plan["floor"])
    report.update(status(wh))
    return report


def status(wh) -> dict:
    with wh.transaction() as c:
        by_status = {r["status"]: int(r["n"]) for r in
                     wh._rows(c.execute("SELECT status, COUNT(*) AS n FROM facility GROUP BY status"))}
        keys = int(wh._rows(c.execute("SELECT COUNT(*) AS n FROM facility_match_key"))[0]["n"])
        events = int(wh._rows(c.execute("SELECT COUNT(*) AS n FROM facility_event"))[0]["n"])
        nxt = _counter_next(wh, c)
    return {"facilities": by_status, "match_keys": keys, "events": events, "next_id": f"IC-{nxt:05d}"}


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    from .warehouse import open_warehouse, SqliteWarehouse
    ap = argparse.ArgumentParser(prog="python -m pipeline.facility_registry")
    ap.add_argument("--db", default=None, help="sqlite path; default: the configured engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="register every golden facility under its current number (idempotent)")
    s.add_argument("--dry-run", action="store_true", help="check coverage and collisions; write nothing")
    sub.add_parser("status", help="facilities by status, keys, events, next IC-number")
    args = ap.parse_args(argv)
    wh = (SqliteWarehouse(Path(args.db)) if args.db
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    try:
        out = seed(wh, dry_run=args.dry_run) if args.cmd == "seed" else status(wh)
    except SeedRefused as e:
        print(f"seed refused, nothing written:\n{e}", file=sys.stderr); return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
