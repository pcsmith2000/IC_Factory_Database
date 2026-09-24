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
    python -m pipeline.facility_registry merge IC-00002 --into IC-00001 --actor NAME --reason "..."
    python -m pipeline.facility_registry retire | repoint KEY --to IC-… | mint  (each --actor, --reason)
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


def _draw(wh, c) -> str:
    """The next IC-number. Postgres: nextval, so no two writers can ever draw the same number.
    A number drawn by a run that later fails is burned, never reused: a gap is harmless and a reuse
    is exactly the fault this registry exists to end."""
    if wh.engine == "postgres":
        n = int(wh._rows(c.execute("SELECT nextval('facility_id_seq') AS n"))[0]["n"])
    else:
        n = _counter_next(wh, c)
        c.execute("UPDATE facility_id_counter SET next = ? WHERE name = 'facility_id'", (n + 1,))
    return f"IC-{n:05d}"


def _event(c, kind: str, facility_id: str, actor: str, reason: str, other: str | None = None,
           match_key: str | None = None, at: str | None = None):
    at = at or _now()
    eid = hashlib.sha256(f"{kind}\x1f{facility_id}\x1f{match_key}\x1f{other}\x1f{at}".encode()).hexdigest()[:20]
    c.execute("INSERT INTO facility_event (event_id, at, kind, facility_id, other_facility_id, match_key, actor, reason) "
              "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (event_id) DO NOTHING",
              (eid, at, kind, facility_id, other, match_key, actor, reason))


class RegistryConflict(RuntimeError):
    """A write would re-point an existing key, or act on a facility that is not live."""


def _roots(wh, c) -> dict[str, str]:
    """facility -> the live facility it resolves to (itself unless merged)."""
    return {r["facility_id"]: (r["merged_into"] or r["facility_id"]) for r in
            wh._rows(c.execute("SELECT facility_id, merged_into FROM facility"))}


class DbIdRegistry:
    """reconcile's id registry, backed by the warehouse (#43). Drop-in for reconcile.IdRegistry.

    get(sig, alts) resolves a cluster:
      1. its signature is a known key            -> that facility (following a merge)
      2. else its other member signatures (alts) name exactly one live facility
                                                  -> attach: the signature becomes that facility's key
      3. else id_registry.json already numbered this signature, and that number is unregistered
                                                  -> adopt: register the plant under its old number
      4. else                                     -> mint a new IC-number from the sequence
    Step 3 matters on the first runs after the seed: the seed registered only golden's 6,426
    plants, while main's file numbers ~90,000 more signatures (T0 leads, excluded rows). Minting
    for those would renumber every one of them; adopting keeps the number they already carry. The
    file maps signature to number one to one, so an adopted number can belong to no other plant.
    A plant that is respelled, or gains an address, keeps its number (step 2); only a plant no key
    has ever named is minted. Keys are only ever added here, never re-pointed: re-pointing is an
    operator act with its own event (repoint / merge below). Nothing is written until save().
    """

    def __init__(self, wh, actor: str = "reconcile", export_path: Path | None = None):
        self.wh, self.actor, self.export_path = wh, actor, export_path
        with wh.transaction() as c:
            self.keys = {r["match_key"]: r["facility_id"] for r in
                         wh._rows(c.execute("SELECT match_key, facility_id FROM facility_match_key"))}
            self.root = _roots(wh, c)
        legacy = (json.loads(export_path.read_text()) if export_path is not None and export_path.exists()
                  else {"ids": {}})
        self.legacy: dict[str, str] = legacy.get("ids", {})
        self.new_keys: list[tuple[str, str, str]] = []      # (key, facility, kind: mint | attach | adopt)
        self.minted: list[str] = []                          # new facility rows: minted or adopted
        self.issued_this_run = 0
        self.adopted_this_run = 0
        self.attached_this_run = 0
        self.ambiguous_this_run = 0

    def _live(self, fid: str) -> str:
        return self.root.get(fid, fid)

    def get(self, sig: str, alts=()) -> str:
        if sig in self.keys:
            return self._live(self.keys[sig])
        known = {self._live(self.keys[a]) for a in alts if a in self.keys}
        if len(known) == 1:
            fid = next(iter(known))
            self.keys[sig] = fid
            self.new_keys.append((sig, fid, "attach"))
            self.attached_this_run += 1
            return fid
        if len(known) > 1:
            self.ambiguous_this_run += 1        # two plants claim its rows: mint, never guess a merge
        old = self.legacy.get(sig)
        if old and old not in self.root and id_number(old) is not None and id_number(old) < ID_FLOOR:
            self.keys[sig] = old
            self.root[old] = old
            self.minted.append(old)
            self.new_keys.append((sig, old, "adopt"))
            self.adopted_this_run += 1
            return old
        with self.wh.transaction() as c:
            fid = _draw(self.wh, c)
        self.keys[sig] = fid
        self.root[fid] = fid
        self.minted.append(fid)
        self.new_keys.append((sig, fid, "mint"))
        self.issued_this_run += 1
        return fid

    def save(self):
        at = _now()
        with self.wh.transaction() as c:
            live = {r["match_key"]: r["facility_id"] for r in wh_rows(self.wh, c,
                    "SELECT match_key, facility_id FROM facility_match_key")}
            clash = [(k, live[k], f) for k, f, _ in self.new_keys if k in live and live[k] != f]
            if clash:
                raise RegistryConflict(f"{len(clash)} keys already name another facility, e.g. {clash[:3]}")
            c.executemany("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                          "VALUES (?, 'active', NULL, ?, ?) ON CONFLICT (facility_id) DO NOTHING",
                          [(f, at, self.actor) for f in self.minted])
            for key, fid, kind in self.new_keys:
                method, conf = key_method(key)
                c.execute("INSERT INTO facility_match_key (match_key, facility_id, method, confidence, source, first_seen) "
                          "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (match_key) DO NOTHING",
                          (key, fid, method, conf, self.actor, at))
                _event(c, kind, fid, self.actor, {
                    "mint": "new plant",
                    "attach": "signature attached: its rows' other keys name this facility",
                    "adopt": "registered under the number id_registry.json already gave this signature",
                }[kind], match_key=key, at=at)
        if self.export_path is not None:
            export_json(self.wh, self.export_path)


def wh_rows(wh, c, sql, params=()):
    return wh._rows(c.execute(sql, params))


def export_json(wh, path: Path):
    """id_registry.json as a read-only export for offline and SQLite runs. It is a superset: keys
    the file already holds are kept (retired numbers stay retired, never reissued), keys the
    registry added are written, and `next` never falls behind the sequence."""
    data = json.loads(path.read_text()) if path.exists() else {"next": 1, "ids": {}}
    with wh.transaction() as c:
        for r in wh_rows(wh, c, "SELECT match_key, facility_id FROM facility_match_key"):
            data["ids"][r["match_key"]] = r["facility_id"]
        data["next"] = max(int(data.get("next") or 1), _counter_next(wh, c))
    path.write_text(json.dumps(data, indent=1, sort_keys=True))


# ---------------------------------------------------------------- operator acts, each an event
def _require_live(wh, c, fid: str) -> dict:
    rows = wh_rows(wh, c, "SELECT facility_id, status, merged_into FROM facility WHERE facility_id = ?", (fid,))
    if not rows:
        raise RegistryConflict(f"{fid} is not a registered facility")
    if rows[0]["status"] != "active":
        raise RegistryConflict(f"{fid} is {rows[0]['status']}" + (f" into {rows[0]['merged_into']}" if rows[0]["merged_into"] else ""))
    return rows[0]


def merge(wh, source: str, into: str, actor: str, reason: str) -> dict:
    """Two numbers were one plant. `source` becomes merged into `into`; its keys stay where they
    are and resolve through merged_into. Anything already merged into `source` is re-rooted, so a
    merge is always one hop and v_assertions_resolved's single join stays exact."""
    if source == into:
        raise RegistryConflict("cannot merge a facility into itself")
    with wh.transaction() as c:
        _require_live(wh, c, source); _require_live(wh, c, into)
        c.execute("UPDATE facility SET status = 'merged', merged_into = ? WHERE facility_id = ?", (into, source))
        c.execute("UPDATE facility SET merged_into = ? WHERE merged_into = ?", (into, source))
        _event(c, "merge", source, actor, reason, other=into)
    return {"merged": source, "into": into}


def retire(wh, fid: str, actor: str, reason: str) -> dict:
    """Not a plant (a closed site, a mistake). The number is never reissued."""
    with wh.transaction() as c:
        _require_live(wh, c, fid)
        c.execute("UPDATE facility SET status = 'retired' WHERE facility_id = ?", (fid,))
        _event(c, "retire", fid, actor, reason)
    return {"retired": fid}


def repoint(wh, key: str, to: str, actor: str, reason: str) -> dict:
    """A key named the wrong plant. The one sanctioned way to move a key; a split is mint + repoint."""
    with wh.transaction() as c:
        _require_live(wh, c, to)
        was = wh_rows(wh, c, "SELECT facility_id FROM facility_match_key WHERE match_key = ?", (key,))
        if not was:
            raise RegistryConflict(f"no such key: {key!r}")
        c.execute("UPDATE facility_match_key SET facility_id = ? WHERE match_key = ?", (to, key))
        _event(c, "repoint", to, actor, reason, other=was[0]["facility_id"], match_key=key)
    return {"key": key, "from": was[0]["facility_id"], "to": to}


def mint(wh, actor: str, reason: str) -> dict:
    """A new plant by hand (the first half of a split)."""
    at = _now()
    with wh.transaction() as c:
        fid = _draw(wh, c)
        c.execute("INSERT INTO facility (facility_id, status, merged_into, created_at, created_by) "
                  "VALUES (?, 'active', NULL, ?, ?)", (fid, at, actor))
        _event(c, "mint", fid, actor, reason, at=at)
    return {"minted": fid}


def is_seeded(wh) -> bool:
    try:
        return bool(wh.query("SELECT 1 FROM facility LIMIT 1"))
    except Exception:
        return False


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
    for name, hlp in (("merge", "SOURCE was the same plant as --into"), ("retire", "FACILITY is not a plant"),
                      ("repoint", "KEY names --to, not the plant it names now"), ("mint", "register a new plant by hand")):
        p = sub.add_parser(name, help=hlp)
        if name in ("merge", "retire", "repoint"):
            p.add_argument("target")
        if name == "merge":
            p.add_argument("--into", required=True)
        if name == "repoint":
            p.add_argument("--to", required=True)
        p.add_argument("--actor", required=True, help="who: a person or a named agent")
        p.add_argument("--reason", required=True, help="why, in a sentence: recorded in facility_event")
    args = ap.parse_args(argv)
    wh = (SqliteWarehouse(Path(args.db)) if args.db
          else open_warehouse(load_yaml(ROOT / "registry" / "config.yaml"), ROOT))
    if wh is None:
        print("warehouse engine is 'none'", file=sys.stderr); return 1
    try:
        if args.cmd == "seed":
            out = seed(wh, dry_run=args.dry_run)
        elif args.cmd == "merge":
            out = merge(wh, args.target, args.into, args.actor, args.reason)
        elif args.cmd == "retire":
            out = retire(wh, args.target, args.actor, args.reason)
        elif args.cmd == "repoint":
            out = repoint(wh, args.target, args.to, args.actor, args.reason)
        elif args.cmd == "mint":
            out = mint(wh, args.actor, args.reason)
        else:
            out = status(wh)
    except SeedRefused as e:
        print(f"seed refused, nothing written:\n{e}", file=sys.stderr); return 2
    except RegistryConflict as e:
        print(f"refused, nothing written: {e}", file=sys.stderr); return 2
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
