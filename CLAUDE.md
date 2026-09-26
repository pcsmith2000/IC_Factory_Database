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

### Credentials in Claude's cloud sessions

The password never lives in the container. The `IC_Database` environment has a proxy credential
for `*.neon.tech` that adds one header to every request: `Neon-Connection-String` with the full
connection string of the `AGENT_READ_WRITE_NON_GOLD` role (not `neondb_owner`). The `DATABASE_URL`
in the container has the host and database but no password; the proxy supplies the real header.

- `missing authentication credentials: required password` means the proxy header did not arrive
  (credential missing or not yet picked up — it applies to new sessions only).
- `permission denied` means the login worked but the role lacks a `GRANT` on that table.
- The credential must not send an `Authorization: Bearer` header: Neon treats a Bearer token as
  Neon Auth / Data API login (the `authenticated` role), not a database password.
- Do not go digging in the proxy's configuration or status to find credentials; if the connection
  fails, report the error and ask the user to fix the environment credential.

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
