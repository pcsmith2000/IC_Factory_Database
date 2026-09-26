# IC Factory Database — notes for Claude

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
