-- agent_read_write_non_gold (AGENT_READ_WRITE_NON_GOLD; Postgres folds unquoted names to lower case):
-- the role agents (Claude sessions, research tools) connect as.
--
-- Reads everything. Writes only the input/queue tables an agent is allowed to touch; every
-- golden and pipeline-derived table (golden_facility, golden_release, conflicts, dim_*,
-- fact_release_metrics, the facility registry, ref_* other than ref_source_row) is read-only,
-- and only the workflows running as the owner change them.
--
-- Run once as neondb_owner in the Neon SQL Editor, after replacing the password placeholder.
-- Create the role here, not in the Neon console: console-created roles join neon_superuser,
-- which would undo the scoping. Not a migration: `pipeline.warehouse init` never runs this.

CREATE ROLE agent_read_write_non_gold LOGIN PASSWORD '<generate-a-strong-password>';

GRANT CONNECT ON DATABASE neondb TO agent_read_write_non_gold;
GRANT USAGE ON SCHEMA public TO agent_read_write_non_gold;
REVOKE CREATE ON SCHEMA public FROM agent_read_write_non_gold;

-- Read: every table and view, now and later (tables the owner creates in future).
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_read_write_non_gold;
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO agent_read_write_non_gold;

-- Write: append-only evidence. golden_dirty needs INSERT too, because the
-- golden_dirty_on_fact trigger on fact_assertions runs as the inserting role.
GRANT INSERT ON fact_assertions, ref_source_row, golden_dirty TO agent_read_write_non_gold;

-- Write: the two review queues. Propose rows, and move their decision columns; nothing else.
GRANT INSERT ON web_research_submission, facility_duplicate_candidate TO agent_read_write_non_gold;
GRANT UPDATE (status, processed_at, report) ON web_research_submission TO agent_read_write_non_gold;
GRANT UPDATE (status, decided_at, decided_by) ON facility_duplicate_candidate TO agent_read_write_non_gold;

-- No DELETE, TRUNCATE or DDL anywhere, and no sequence access (facility ids are minted by the
-- registry workflows only).

-- Check (run as the owner): every table the role can write, and how.
-- SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type)
--   FROM information_schema.role_table_grants
--  WHERE grantee = 'agent_read_write_non_gold' AND privilege_type <> 'SELECT'
--  GROUP BY 1 ORDER BY 1;
