# IC Factory Database — notes for Claude

## Intent (DRAFT: owner to confirm; read this before any change)

Several agents work on this repository and on ADL_Viz at once: pipeline developers, the web-research
agent, the hourly monitor, and ADL_Viz developers. This section is the goal they share. When a task
and this section disagree, stop and ask; don't trade the intent for the task.

### What we are building
A trustworthy census of every **industrialized-construction manufacturing plant in the United States**
(modular, volumetric, panelised, precast, mass timber, truss and component plants; not integrated
circuits). For each plant it records where it is, what it makes and how much it can make. ADL Ventures
uses it to reason about national, regional and local IC capacity, through the ADL_Viz map and the
golden table.

**The product is the golden table** (`golden_facility`), and every number quoted from it carries a
release tag. Everything else (sources, assertions, enrichment, web research, the monitor) exists to
make that table more correct, more complete and more current, in that order.

### Priorities, in order: when two conflict, the higher one wins
1. **Never publish a false fact.** An empty cell is better than a plausible wrong one: don't fabricate
   or guess a value. A plant leaves golden only on evidence (a person, or two sources, or one
   registry-grade source). Losing a real plant is as bad as adding a fake one.
2. **Every value is traceable.** A golden value names the assertion that won, the assertion names its
   source and document row, and that row names its URL, retrieval date and position. Work that
   breaks this chain is not done, however good the data looks.
3. **Identity is permanent.** An IC-number means one plant forever. Merges and retirements go
   through `facility_event`; a number is never re-pointed or reused, and assertions are never
   rewritten, only appended.
4. **People outrank machines.** ADL employee feedback and operator corrections beat every automated
   source. The monitor, web research, classifiers and enrichment fill and correct, and they never
   overrule a person.
5. **Coverage.** Find the plants we are missing, measured honestly against the Census frame and the
   control list. Never steer the measurement.
6. **Freshness.** A new fact should reach golden within minutes (golden-refresh), not at the next
   quarterly run.
7. **Convenience.** Speed, cost and neatness of the automation come last.

### Red lines: no agent crosses these without the owner's written go-ahead
- Writing to `golden_facility` or `golden_release` directly. Golden is computed, never edited.
- Changing an IC-number's meaning: re-pointing, reusing or renumbering one, or editing `id_registry.json`.
- Deleting or rewriting assertions, source rows or events. Correct a value by appending a new assertion.
- `DROP` / `TRUNCATE` / `ALTER` or bulk `DELETE` / `UPDATE` on the live warehouse.
- Changing survivorship order (`registry/survivorship.yaml`, `golden._rank`), gate thresholds, the
  frozen prompts, or the control list, except in a PR that states the before/after effect on golden.
- Ruling a plant out of scope (`existence_flag = not_ic` or `closed`) without cited evidence.
- Publishing credentials, or sending warehouse data anywhere outside Neon, GitHub, Vercel and the
  ADL_Viz deployment.

### How agents work together
- **GitHub issues are the shared memory.** Before starting, search the open issues and branches for
  the same work; after finishing, leave the outcome on the issue. One issue per problem.
- **Every data correction goes through a versioned path**, never ad-hoc SQL:
  - a person's correction: `control/operator_assertions.csv` or ADL_Viz feedback;
  - a monitor fix: `control/monitor_fix_assertions.csv` (see below);
  - web research: `web_research_submission`;
  - merges: the `duplicates` workflow.
- **Small, reversible changes may merge on green CI** after an issue describes them: a monitor fix,
  a workflow bug, a doc. Anything that changes identity, survivorship, scope or the schema is
  proposed in a PR and waits for the owner.
- **Measure before and after.** A PR that changes what golden contains states how many rows and
  fields it moves, checked read-only against the live warehouse or on a Neon branch.
- **Say what you did not do.** Skipped, ambiguous or unverified items are listed, not dropped.

### Decisions reserved for the owner
Scope boundaries (for example NAICS 332312 structural steel, PEMB), the confidence floor for
publishing a capability, identity disputes (#58), removing a source, and anything that changes the
headline counts in the README.

<!-- OPEN QUESTIONS for the owner, remove once answered:
  1. Is "US only" right, or should Canadian/Mexican plants that ship into the US be kept (ca_hcd has some)?
  2. Is capacity (throughput, utilisation, vacancy) a goal of the golden table, or only of the ADL_Viz vendor feed?
  3. Who besides ADL Ventures consumes the data, and does anything outside ADL_Viz read it?
  4. The freshness target: minutes, hourly or daily?
-->

## Querying the Neon warehouse

The live warehouse is Neon Postgres, endpoint `ep-fragrant-snow-awfbmy0k.c-12.us-east-1.aws.neon.tech`,
database `neondb`. The GitHub Actions workflows write to it.

Use Neon's HTTPS SQL endpoint, not a Postgres socket: port 5432 is blocked in Claude's cloud sessions.

```bash
python -m pipeline.neon_sql "SELECT count(*) FROM golden_facility"
python -m pipeline.neon_sql --json "SELECT ..." "SELECT ..."        # several statements, one transaction
python -m pipeline.neon_sql --write "UPDATE ..."                    # writes: see rules below
```

`pipeline/neon_sql.py` resolves the connection from `DATABASE_URL` if set, otherwise from `NEON_API_KEY`
(project from `NEON_PROJECT_ID`, the git-ignored `.neon` file, or the key's own project; role from
`NEON_ROLE`, default the database owner). It warns when the host is not the live warehouse: stop and
say so rather than trusting results from another project. Under the hood it POSTs to
`https://<host>/sql` with the `Neon-Connection-String` header and `Neon-Batch-Read-Only: true`.

### Rules

- **Reads:** Claude may run read-only queries at any time. They run in a read-only transaction.
- **Writes:** Claude may write with `--write` only after stating the exact SQL and why, and only
  small, targeted changes (append to `fact_assertions`, queue rows in `golden_dirty`, update a
  `facility_duplicate_candidate` or `web_research_submission` status). Never `DROP`, `TRUNCATE`,
  `ALTER`, or bulk `DELETE`/`UPDATE` against the live warehouse.
- **Bulk operations** (golden refresh, duplicate merges, web-research ingest) run through the
  GitHub Actions workflows (`golden-refresh.yml`, `duplicates.yml`, `web-research-ingest.yml`),
  not ad-hoc SQL. For anything risky, test on a Neon branch first.
- Never print, log or commit connection strings, passwords or API keys.

## Fixing data defects: `control/monitor_fix_assertions.csv`

A data defect a source shipped (a wrong state, a malformed ZIP) is fixed by a line in
`control/monitor_fix_assertions.csv`, not by SQL:

```
facility_id,field,value,retrieved_date,issue,evidence
IC-95437,state,NV,2026-09-26,https://github.com/pcsmith2000/IC_Factory_Database/issues/57,ZIP 89115 and city North Las Vegas are NV; ...
```

- Every line names the GitHub issue that describes the defect and the evidence for the value.
- Only `address, city, state, zip, website, phone, email` may be fixed. Never `existence_flag`,
  `lat_lon`, names or capabilities: those need a person (`control/operator_assertions.csv`).
- Source `monitor_fix` ranks directly below people (`adl_employee_feedback`, `operator`) and above every other
  source, site visits and web-research overrides included. Check `conflicts` for fixes a newer source disputes.
- `python -m pipeline.monitor_fix --check` validates the file, and CI's tests do too. When the file
  changes on main, `monitor-fix.yml` appends the lines to `fact_assertions` (idempotently), and the
  next `golden-refresh` brings them into golden. To undo a fix, add a line with the right value and a later date.
