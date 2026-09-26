# Web research pipeline evaluation: pass ledger

`passes.jsonl` holds one line per event of the evaluation (docs/web-research-pipeline-eval.md §6):
the benchmark export, then every pass with its commit, benchmark hash, change, batch, billed and
list-price cost, cumulative list cost, and the §4 metrics. Configurations live in `configs/`.

No benchmark, reference payload or holdout output is ever committed here (the repository is public).
