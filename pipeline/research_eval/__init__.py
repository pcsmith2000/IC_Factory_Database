"""Low-cost web research pipeline: the evaluation harness (docs/web-research-pipeline-eval.md).

    benchmark   export the frozen benchmark from the warehouse, read-only (inputs, labels, splits)
    pipeline    the pipeline under test: crawl, cached search, regex, one model call, quote and contract checks
    gateway     AI Gateway calls, catalog prices, and the per-pass cost ceiling
    score       the section-4 metrics, adjudication, scorecard.json and the job summary

Nothing here writes to the warehouse. The pipeline never sees labels.json: it reads only the
benchmark's inputs file, and the scorer reads the labels after the pipeline has finished.
"""
