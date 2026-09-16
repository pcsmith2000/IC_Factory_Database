"""Pick the classifier model by measurement instead of argument.

G5 already is an eval harness: 60 hand-labelled seeds, precision >= 0.95, recall >= 0.90. This
runs candidate models over those seeds alone — one batch, ~60 rows — scores each with the same
gate the release uses, and prices it against the gateway's own published rates. The cheapest
model that clears the gate is the answer; everything above it is money spent on nothing.

    python -m pipeline.bakeoff --models anthropic/claude-haiku-4.5,openai/gpt-oss-20b
    python -m pipeline.bakeoff --list            # candidates, cheapest first, no calls made
    python -m pipeline.bakeoff --estimate        # what a full run would cost, no calls made

Needs AI_GATEWAY_API_KEY (or ANTHROPIC_API_KEY). Each scored model costs roughly a cent: the
seeds are ~60 rows, against ~12,973 for a full run. Scoring ten models is cheaper than one run.

The seeds are graded here in a single 60-row batch, which is NOT the shape a real run uses —
classify.batches() spreads them two per batch through the candidates. A model that clears the
gate here is a candidate, not a proven choice; the proof is a full run's own G5.
"""
from __future__ import annotations
import argparse, json, sys, urllib.request
from pathlib import Path

from . import ai_client_and_model, gateway_model_id
from .classify import classify_batch, prompt_hash
from .gates import g5_classifier_eval
from .registry import load_yaml

ROOT = Path(__file__).resolve().parent.parent
MODELS_URL = "https://ai-gateway.vercel.sh/v1/models"
# Full-run shape, measured on the 2026-09-16 candidate set AFTER families 3219 and 3212 were
# admitted whole (classify.WIDE_NAICS_FAMILIES): ~13,000 rows in ~130 batches of 100, with the
# frozen prompt re-sent each batch. Before the widening it was 2,922 rows in 30 batches, 150k in
# / 90k out — so any full-run price quoted before 2026-09-16 is roughly a quarter of the truth.
# Used only to turn a per-seed price into the number that matters, cost per quarterly run.
FULL_RUN_INPUT_TOKENS = 660_000
FULL_RUN_OUTPUT_TOKENS = 400_000


def catalogue(retries: int = 3) -> dict[str, tuple[float, float]]:
    """{model_id: (input $/token, output $/token)} from the gateway. Needs no authentication.

    Retried like every other network read here — a transport blip fetching the price list should
    not throw away a bake-off that is about to spend money on model calls.
    """
    import time
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(MODELS_URL, timeout=60) as r:
                data = json.load(r)["data"]
            break
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)
    out = {}
    for m in data:
        p = m.get("pricing") or {}
        if m.get("type") == "language" and p.get("input"):
            out[m["id"]] = (float(p["input"]), float(p.get("output") or 0))
    return out


def _is_infrastructure(e: Exception) -> bool:
    """True when the gateway never let the model answer — rate limit, auth, transport, 5xx.

    Distinct from a model that answered badly. Scoring several models back to back trips the
    gateway's burst limit — the 429 names a tier, but the same model answers on a later call — and
    a rate limit reached by our own pacing is not evidence about a model.
    """
    name = type(e).__name__
    if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError",
                "AuthenticationError", "PermissionDeniedError", "NotFoundError"):
        return True
    return any(t in str(e) for t in ("429", "rate-limited", "rate limit", "503", "502", "504"))


def _combine(runs: list[dict]) -> dict:
    """Fold repeated scores of one model into one verdict, reported at its worst.

    A model passes only if every run passed, and the figures shown are the minimum seen. Run-to-run
    variance is real at temperature 0 — the same model cleared recall 100% and then 83% on
    consecutive scores — and a mean would hide exactly the instability that matters for a gate a
    release depends on.
    """
    r = dict(runs[0])
    r["runs"] = len(runs)
    r["passed"] = all(x["passed"] for x in runs)
    r["precision"] = min(x["precision"] for x in runs)
    r["recall"] = min(x["recall"] for x in runs)
    r["recall_range"] = (min(x["recall"] for x in runs), max(x["recall"] for x in runs))
    r["precision_range"] = (min(x["precision"] for x in runs), max(x["precision"] for x in runs))
    costs = [x["seed_cost"] for x in runs if x.get("seed_cost") is not None]
    r["seed_cost"] = sum(costs) / len(costs) if costs else None
    r["reasks"] = sum(x.get("reasks", 0) for x in runs)
    r["contract_errors"] = sum(x.get("contract_errors", 0) for x in runs)
    return r


def _cost(price: tuple[float, float], tin: int, tout: int) -> float:
    return price[0] * tin + price[1] * tout


def score(model: str, seeds: list[dict], prompt: str, temperature: float,
          prices: dict) -> dict:
    """One model over the seed set: the G5 verdict, plus what it cost and would cost."""
    usage: dict = {}
    # Re-asks are a first-class result, not a detail. gpt-oss-120b cleared G5 and still needed a
    # re-ask on roughly a quarter of the 100-row batches in run 35160444815 — every one recovered,
    # but each costs an extra call, and a model that cannot hold the output contract is a model to
    # replace. A score that hides that is measuring half the question.
    contract: dict = {}
    labels_list = classify_batch(seeds, prompt, model, temperature, usage_out=usage, stats=contract)
    labels = {o["row_hash"]: o for o in labels_list}
    gate = g5_classifier_eval(labels, seeds, 0.95, 0.90)
    d = gate.details or {}
    mid = gateway_model_id(model)
    price = prices.get(mid)
    tin, tout = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    return {
        "model": mid, "passed": gate.passed, "precision": d.get("precision", 0.0),
        "recall": d.get("recall", 0.0), "tp": d.get("tp"), "fp": d.get("fp"), "fn": d.get("fn"),
        "unlabelled": sum(1 for s in seeds if s["row_hash"] not in labels),
        "seed_tokens": (tin, tout),
        "reasks": contract.get("reasks", 0), "contract_errors": contract.get("contract_errors", 0),
        "seed_cost": _cost(price, tin, tout) if price else None,
        "full_run_cost": _cost(price, FULL_RUN_INPUT_TOKENS, FULL_RUN_OUTPUT_TOKENS) if price else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline.bakeoff")
    ap.add_argument("--models", help="comma-separated gateway model ids; default: config's pinned model")
    ap.add_argument("--list", action="store_true", help="candidates by full-run cost, make no calls")
    ap.add_argument("--estimate", action="store_true", help="price the named models, make no calls")
    ap.add_argument("--repeat", type=int, default=3,
                    help="scores per model (default 3). One is not a verdict: gpt-oss-120b scored "
                         "recall 100%% then 83%% on consecutive runs at temperature 0.")
    ap.add_argument("--delay", type=float, default=20.0,
                    help="seconds between models (default 20). The gateway burst-limits a run that "
                         "scores several back to back, and a 429 costs a verdict.")
    args = ap.parse_args(argv)

    cfg = load_yaml(ROOT / "registry" / "config.yaml")
    prices = catalogue()

    if args.list:
        ranked = sorted(prices.items(), key=lambda kv: _cost(kv[1], FULL_RUN_INPUT_TOKENS, FULL_RUN_OUTPUT_TOKENS))
        print(f"{'full run':>9}  model")
        for mid, pr in ranked[:40]:
            print(f"${_cost(pr, FULL_RUN_INPUT_TOKENS, FULL_RUN_OUTPUT_TOKENS):>8.2f}  {mid}")
        return 0

    models = [m.strip() for m in (args.models or cfg["classifier"]["model"]).split(",") if m.strip()]

    if args.estimate:
        print(f"{'full run':>9}  model")
        for m in models:
            mid = gateway_model_id(m)
            pr = prices.get(mid)
            print(f"${_cost(pr, FULL_RUN_INPUT_TOKENS, FULL_RUN_OUTPUT_TOKENS):>8.2f}  {mid}" if pr
                  else f"{'?':>9}  {mid}  (not in the gateway catalogue)")
        return 0

    seeds_path = ROOT / "control" / "seeds.csv"
    import csv
    seeds = list(csv.DictReader(open(seeds_path, newline="", encoding="utf-8")))
    if not seeds:
        print("control/seeds.csv is empty — nothing to score against", file=sys.stderr); return 1
    prompt = (ROOT / cfg["classifier"]["prompt_path"]).read_text()
    temp = cfg["classifier"]["temperature"]
    print(f"{len(seeds)} seeds · prompt {prompt_hash(ROOT / cfg['classifier']['prompt_path'])} · "
          f"gate: precision >= 95%, recall >= 90%\n")

    import time
    results = []
    for n, m in enumerate(models):
        if n:
            time.sleep(args.delay)   # pace ourselves rather than collect 429s and call them results
        try:
            ai_client_and_model(m)   # fail fast and identically for every model
        except RuntimeError as e:
            print(e, file=sys.stderr); return 1
        try:
            runs = []
            for k in range(args.repeat):
                if k:
                    time.sleep(args.delay)
                runs.append(score(m, seeds, prompt, temp, prices))
            results.append(_combine(runs))
        except Exception as e:
            # A model that cannot hold the output contract has failed the bake-off. A model the
            # gateway rate-limited or could not reach has not been measured at all, and recording
            # that as a failure would retire a candidate on the strength of an account limit.
            kind = "UNTESTED" if _is_infrastructure(e) else "FAIL"
            print(f"  {gateway_model_id(m):<44} {kind}  {type(e).__name__}: {str(e)[:230]}")
            results.append({"model": gateway_model_id(m), "passed": False, "untested": kind == "UNTESTED",
                            "error": f"{type(e).__name__}: {str(e)[:150]}",
                            "precision": 0.0, "recall": 0.0, "full_run_cost": None})

    print(f"\n{'':9}{'model':<42}{'prec':>7}{'recall':>8}{'seed $':>9}{'run $':>9}{'re-ask':>8}")
    for r in sorted(results, key=lambda x: (not x["passed"], x.get("untested", False),
                                            x.get("full_run_cost") or 9e9)):
        mark = "PASS" if r["passed"] else ("UNTESTED" if r.get("untested") else "FAIL")
        sc = f"${r['seed_cost']:.4f}" if r.get("seed_cost") is not None else "-"
        fc = f"${r['full_run_cost']:.2f}" if r.get("full_run_cost") is not None else "-"
        spread = ""
        for label, key in (("recall", "recall_range"), ("prec", "precision_range")):
            rg = r.get(key)
            if rg and rg[0] != rg[1]:
                spread += f"   {label} {rg[0]:.0%}-{rg[1]:.0%} over {r['runs']} runs"
        ra = f"{r.get('reasks', 0)}" + (f"/{r['contract_errors']}!" if r.get("contract_errors") else "")
        print(f"{mark:<9}{r['model']:<42}{r['precision']:>6.0%}{r['recall']:>8.0%}{sc:>9}{fc:>9}{ra:>8}"
              + (f"   {r['error']}" if r.get("error") else "") + spread)
    winner = next((r for r in sorted(results, key=lambda x: x.get("full_run_cost") or 9e9) if r["passed"]), None)
    untested = [r["model"] for r in results if r.get("untested")]
    print()
    if untested:
        print(f"not measured ({len(untested)}): {', '.join(untested)}")
        print("  rate limit or transport, not a verdict — re-run these before ruling them out\n")
    if winner:
        print(f"cheapest model clearing G5: {winner['model']}  (${winner['full_run_cost']:.2f} per full run)")
        print(f"pin it in registry/config.yaml as classifier.model")
    else:
        print("no candidate cleared G5 — widen the field, or improve the prompt and re-score")
    return 0


if __name__ == "__main__":
    sys.exit(main())
