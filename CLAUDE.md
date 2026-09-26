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
- Agent sessions should connect as `agent_read_write_non_gold` (`docs/roles/agent_read_write_non_gold.sql`):
  it reads everything and can write only the non-golden tables above, so the database enforces
  these rules too. The owner role belongs in the GitHub Actions secrets, not in agent environments.
- Never print, log or commit connection strings, passwords or API keys.
