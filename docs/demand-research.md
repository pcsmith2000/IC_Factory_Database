# Demand research: projects built with the plants in golden

The supply side knows, plant by plant, what can be built. The demand side asks what is being built with it: the
projects each manufacturer's modules went into, are going into, or are planned to go into. This document is the
design of the first demand pipeline and the loop that tunes it. It starts small: a handful of well-known volumetric
manufacturers, their projects, one new set of tables.

The rules in `CLAUDE.md` apply unchanged. **Veracity:** every value carries the exact words that state it and the page
they are on; an empty field beats a guess. **Comprehensiveness:** a real project missing is a defect, as is a real
project split into two.

## 1. The unit: a project

One row per project: a named building or development, with where it is, what it is (segment, units, stories, modules,
floor area), where it stands (status and when that was said), who is behind it (developer, contractor, architect)
and which manufacturer supplied it. A project differs from a plant in three ways that shape everything below:

- **There is no list to start from.** Projects are discovered, so recall (did we find it at all) matters as much as
  precision.
- **One project has many names.** A developer's name, a building name, a street address, a phase. Merging mentions is
  a judgement, and a wrong merge loses a project while a missed merge double-counts demand.
- **Facts age.** Status moves (announced, under construction, completed, stalled, cancelled), so every status carries
  the page it came from and that page's date when stated.

## 2. Tables (`pipeline/demand/load.py`)

| Table | One row per | Notes |
| --- | --- | --- |
| `demand_project` | project | `DP-000001` onwards, never reissued. `review_status` starts `proposed`; only a person moves it to `confirmed`, `rejected` or `merged`. |
| `demand_project_key` | name (and name + state, address + state) a project was found under | A later pass that finds the project again adds evidence to it instead of minting a new one. |
| `demand_assertion` | (project, field, value, page) | Value, verbatim quote, URL, fetched time, run, config. Conflicting values are all kept. Field `supplier` is the tie to the manufacturer; its `facility_ids` are the manufacturer's plants in golden, not a claim about which plant built it. |

The tables are append-only from the pipeline. Nothing here writes golden, `fact_assertions` or any supply table.

## 3. The pipeline (`pipeline/demand/pipeline.py`, v0)

Input: a batch file (`pipeline/demand/batches/*.json`) naming manufacturers, their golden facility ids, aliases and
website. Per manufacturer:

1. **Crawl** the manufacturer's own site, free: homepage, project/portfolio/news index pages, then the pages those
   list, up to 30.
2. **Search**, paid: a few configured queries naming the company (Tako via `gemini-3.1-flash-lite`, counted only
   when the gateway confirms the tool call); the top results are fetched.
3. **Select**, free: own-site pages that read like a project, third-party pages that name the company; duplicates
   and pages without project words are skipped, and the rest capped.
4. **Extract**: one model call per page (`claude-sonnet-5` in v0). Every project the page ties to this
   manufacturer, every field with the exact words that state it.
5. **Verify**, deterministic: each quote must occur verbatim in the fetched page and must contain its value
   (numbers, state names, names); a field that fails is dropped, and a project without a verified name or a
   verified tie to the manufacturer is dropped. Status and segment are judgements, so their quote is the evidence.
6. **Merge**: mentions grouped by a normalised name key, then one model call per manufacturer that groups aliases.
   Every value keeps its own quote and URL; conflicts are listed.

Output: `projects.json`, `audit.md` (every project with every quote, every page sent to the model, every dropped field
and why, cost), `costs.jsonl`, and per-manufacturer folders with the raw model replies. The pipeline has no
database code. `pipeline.demand.load` writes a pass to the tables, and only when asked (`load=yes`).

Workflow: `demand-research.yml`, dispatch only. The pass job has the gateway key and no database credentials; the
load job has the database and no gateway key.

## 4. Measuring it

There is no earlier agent's work to compare against, so the reference is built:

- **Reference set.** For each manufacturer in the batch, projects found by a careful research agent (or a person),
  each field with a URL and verbatim quote, including stalled and cancelled projects. It is scored against, never put
  in a prompt, and kept out of git beyond its metrics (the repository is public). ADL's own knowledge of projects is
  the best anchor and should be added when available.
- **Metrics per pass**
  - **R1 recall**: reference projects the pass found (matched by manufacturer and name/alias/address).
  - **P1 precision**: pass projects that are real projects of that manufacturer. Those in the reference count as
    correct; the rest are checked by reading their evidence, and a wrong one (another company's project, a factory,
    a product line) is listed.
  - **S1 wrong ties**: a project attributed to the wrong manufacturer. Target 0; any one stops the loop.
  - **F1 field agreement** with the reference on city, state, units, segment and status.
  - **D1 split projects**: one reference project found as two pass projects (merge misses); **D2 joined projects**:
    two reference projects merged into one.
  - **C1 list cost per manufacturer** and **C2 per verified project**.
- **Gate for loading.** A configuration may load into the warehouse (as `proposed`) when S1 is 0, P1 is at least 0.95
  and R1 is at least 0.70 on the batch, and the user says go.

## 5. The loop

Run by a Claude session on branch `claude/...` (or `eval/demand-research`), with `/loop`. Each wake-up does one step.

**Hard limits (ask the user instead of crossing):**

- **Money.** Each pass sets `max_cost_usd` (the workflow's ceiling is $10). The evaluation's total is the user's
  number; stop at 80% of it. The gateway key is shared: check no other workflow is using it.
- **Models.** Anything at or below $2/M input and $10/M output is allowed (Sonnet 5 sits on that line). Opus 5.5 and
  other models above it need the user's approval.
- **Warehouse.** No writes during tuning. Loading (`load=yes`) needs the user's go, and loads only `proposed` rows.
- **Veracity.** Never relax the verbatim-quote or tie rule to lift recall. A project found by reasoning rather than
  stated on a page is not a finding.

**Each pass:** state a hypothesis in the config; change one thing; dispatch; read `audit.md`, not only the metrics;
append the pass to `research/demand-eval/passes.jsonl` (config, commit, cost, metrics) and what was learned to
`research/demand-eval/LEARNINGS.md`.

**Things worth testing first:** cheaper extraction models against Sonnet 5 on the same cached pages (tokens only);
more searches per manufacturer and which queries find third-party projects the site omits; developer and news pages
as a second hop from a found project (a project page often names the developer, whose site lists the rest);
PDF case-study sheets; status freshness (a second, dated source for anything not completed).

**Stop and ask if:** S1 > 0; a pass overspends its ceiling by more than 10%; anything seems to need a warehouse write,
more money or a model above the line.
