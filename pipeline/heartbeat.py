"""A progress heartbeat the run publishes where an operator can actually read it.

GitHub will not serve logs for an *in-progress* job over its REST API — only the browser's own
streaming endpoint sees them. So anything watching a run from outside (an agent, a script, a
dashboard) is blind until the job ends, which made a healthy 63-minute classification
indistinguishable from a hang. The only way to read run 35160444815's progress was to cancel it
and throw away seventeen batches of paid-for work.

The fix is not more logging. It is publishing state somewhere readable while the run is alive, and
the Blob store is already wired into every run. Layer 3 writes its position there every few
seconds; anyone with the token can watch.

    python -m pipeline.heartbeat                 # newest run
    python -m pipeline.heartbeat <run_id>        # one run
    python -m pipeline.heartbeat --list          # every run the store holds
    python -m pipeline.heartbeat --watch         # poll the newest until it stops running

Three rules this module keeps:
  - It never raises into the pipeline. A broken heartbeat must not break a run; every write is
    wrapped and failures are counted, not propagated.
  - It is throttled. One write per `every` seconds, except the first, a phase change, and the
    terminal state, which always go out.
  - It is a no-op without a blob token, so local runs and tests cost nothing.
"""
from __future__ import annotations
import json, os, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "ic-runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Heartbeat:
    """Publishes {PREFIX}/<run_id>/progress.json while the run is alive."""

    def __init__(self, cfg: dict, run_id: str | None = None, every: float = 10.0):
        self.run_id = run_id or os.environ.get("GITHUB_RUN_ID") or datetime.now(timezone.utc).strftime("local-%Y%m%dT%H%M%S")
        self.every, self.state, self._last, self.errors = every, {}, 0.0, 0
        self.started = time.time()
        try:
            from .archive import open_archive
            self.archive = open_archive(cfg)
        except Exception:
            self.archive = None          # no token, no store: silently inert
        self.state.update({"run_id": self.run_id, "started": _now(), "status": "running",
                           "url": (f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{self.run_id}"
                                   if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID") else None)})

    @property
    def key(self) -> str:
        return f"{PREFIX}/{self.run_id}/progress.json"

    def beat(self, phase: str | None = None, force: bool = False, **fields) -> None:
        if phase and phase != self.state.get("phase"):
            force = True                 # a phase change is always worth a write
        if phase:
            self.state["phase"] = phase
        self.state.update(fields)
        self.state["updated"] = _now()
        self.state["elapsed_s"] = round(time.time() - self.started, 1)
        if not force and (time.time() - self._last) < self.every:
            return
        self._write()

    def done(self, **fields) -> None:
        self.state["status"] = "done"; self.beat(force=True, **fields)

    def failed(self, error: str, **fields) -> None:
        self.state["status"] = "failed"; self.state["error"] = str(error)[:600]
        self.beat(force=True, **fields)

    def _write(self) -> None:
        if self.archive is None:
            return
        try:
            tmp = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / f"hb-{self.run_id}.json"
            tmp.write_text(json.dumps(self.state, indent=1))
            self.archive.put(tmp, self.key)
            self._last = time.time()
        except Exception:
            # Deliberately silent. A store that is unreachable is a reason to lose visibility,
            # never a reason to lose the run.
            self.errors += 1


# ---- reading, for whoever is watching

def read(cfg: dict, run_id: str | None = None) -> dict | None:
    from .archive import open_archive
    a = open_archive(cfg)
    if a is None:
        return None
    blobs = [b for b in a.list_prefix(f"{PREFIX}/") if b["pathname"].endswith("progress.json")]
    if run_id:
        blobs = [b for b in blobs if f"/{run_id}/" in b["pathname"]]
    if not blobs:
        return None
    b = max(blobs, key=lambda x: x.get("uploadedAt") or x["pathname"])
    with urllib.request.urlopen(b["url"], timeout=60) as r:
        return json.load(r)


def render(s: dict) -> str:
    lab = s.get("labels") or {}
    tot = sum(lab.values())
    bits = [f"run {s.get('run_id')} · {s.get('status')} · {s.get('phase') or '?'}",
            f"  elapsed {s.get('elapsed_s', 0)/60:.1f}m   updated {s.get('updated')}"]
    if s.get("batches_total"):
        pct = s.get("batches_done", 0) / s["batches_total"]
        bits.append(f"  batch {s.get('batches_done')}/{s['batches_total']} ({pct:.0%})"
                    f"   {s.get('secs_per_batch', 0):.0f}s/batch   eta {s.get('eta_s', 0)/60:.1f}m")
    if tot:
        bits.append(f"  IC {lab.get('IC',0)} ({lab.get('IC',0)/tot:.0%})  NOT-IC {lab.get('NOT-IC',0)}"
                    f"  UNCERTAIN {lab.get('UNCERTAIN',0)}  of {tot} labelled")
    if s.get("reasks") or s.get("contract_errors"):
        bits.append(f"  {s.get('reasks',0)} re-asks, {s.get('contract_errors',0)} unusable responses")
    if s.get("error"):
        bits.append(f"  ERROR {s['error']}")
    if s.get("url"):
        bits.append(f"  {s['url']}")
    return "\n".join(bits)


def main(argv=None) -> int:
    import argparse
    from .registry import load_yaml
    ap = argparse.ArgumentParser(prog="python -m pipeline.heartbeat")
    ap.add_argument("run_id", nargs="?", help="default: the most recently updated run")
    ap.add_argument("--list", action="store_true", help="every run the store holds")
    ap.add_argument("--watch", action="store_true", help="poll until the run stops running")
    ap.add_argument("--interval", type=float, default=30.0)
    a = ap.parse_args(argv)
    cfg = load_yaml(ROOT / "registry" / "config.yaml")

    if a.list:
        from .archive import open_archive
        arch = open_archive(cfg)
        for b in sorted(arch.list_prefix(f"{PREFIX}/"), key=lambda x: x["pathname"]):
            if b["pathname"].endswith("progress.json"):
                print(f"  {b['pathname'].split('/')[1]}")
        return 0

    while True:
        s = read(cfg, a.run_id)
        if not s:
            print("no heartbeat found — the run may predate this, or hold no blob token", file=sys.stderr)
            return 1
        print(render(s))
        if not a.watch or s.get("status") != "running":
            return 0
        print()
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
