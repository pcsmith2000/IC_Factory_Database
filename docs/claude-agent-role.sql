-- A login for Claude sessions: read everything, write only the small, targeted changes CLAUDE.md
-- allows (append assertions, queue golden_dirty, set a duplicate or submission status). No DELETE,
-- no TRUNCATE, no DDL. Bulk work (golden refresh, merges, ingest) stays in the Actions workflows,
-- which keep using the owner's connection.
-- NOT a migration (warehouse init does not run it): the owner runs it once, by hand. Test it on a
-- Neon branch first. Replace the password; give Claude's environment only the connection string of
-- THIS role, never the owner's.
--
--   postgresql://claude_agent:<password>@<host>/neondb?sslmode=require
CREATE ROLE claude_agent LOGIN PASSWORD 'CHANGE_ME' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE neondb TO claude_agent;
GRANT USAGE ON SCHEMA public TO claude_agent;

-- Read everything, including tables the owner creates later.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_agent;
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public GRANT SELECT ON TABLES TO claude_agent;

-- Append assertions with their provenance rows. The golden_dirty_on_fact trigger runs as the
-- inserting role (not SECURITY DEFINER), so it needs INSERT on golden_dirty as well.
GRANT INSERT ON fact_assertions, ref_source_row, dim_date, golden_dirty TO claude_agent;

-- Status changes only: decide a duplicate pair, or re-queue / reject a submission.
GRANT UPDATE (status, decided_at, decided_by) ON facility_duplicate_candidate TO claude_agent;
GRANT UPDATE (status, processed_at, report) ON web_research_submission TO claude_agent;

ALTER ROLE claude_agent SET statement_timeout = '60s';

-- Check (as claude_agent): each of these must fail with "permission denied".
--   DELETE FROM golden_dirty WHERE false;
--   UPDATE golden_facility SET name = name WHERE false;
--   CREATE TABLE claude_agent_probe (x int);
