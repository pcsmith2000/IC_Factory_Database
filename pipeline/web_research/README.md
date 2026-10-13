# Web research

The modules here take web research into the warehouse. The research itself, its prompts, and its
evaluation are in `pipeline/research_eval/`.

- `production.py`: production batches (`select`, `health`, `submit`), run by
  `research-production.yml`. See `docs/web-research-production-loop.md`.
- `ingest.py`: validates pending `web_research_submission` documents against the contract in
  `docs/web-research-agent.md` and writes what passes to `fact_assertions` as source `web_research`.
  It is run by `web-research-ingest.yml`.
- `pages.py`: an SSRF-safe URL opener and decoders for public obfuscated contacts, used by
  `research_eval`'s page fetcher, plus `norm`, which coordinate recovery uses.

## History: the Tako AI Search pass (September 2026)

The first web-research pass was a one-off campaign that used Tako AI Search on the 241 ADL/4ward
facilities. The import run 35547749508 committed 179 reviewed assertions for 88 facilities as source
`tako_ai_search`. Its receipt and reviewed inputs are in `research/adl-2026-09-20/`. That source
stays in the warehouse and in survivorship, where it ranks below every listed and unlisted source
(`registry/survivorship.yaml`). The campaign's workflows and modules were removed in October 2026.
They are in git history before that removal.
