-- AGENT_READ_WRITE_NON_GOLD: a login for agents that read the whole warehouse and add or correct
-- pipeline rows, but never touch golden. No DELETE, no TRUNCATE, no DDL anywhere.
-- NOT a migration (warehouse init does not run it): the owner runs it once, by hand. Test it on a
-- Neon branch first.
--
-- STEP 0 (Neon console, not SQL): delete the existing AGENT_READ_WRITE_NON_GOLD role.
--   A role made in the console or API is always a member of neon_superuser (granted by cloud_admin),
--   which bypasses every GRANT below, and neondb_owner cannot revoke that membership. A role made
--   with CREATE ROLE by neondb_owner is not a member, so its grants hold.
--   Anything connecting as the old role stops working until it gets the new connection string.
--
-- Then run the rest as neondb_owner. The name is quoted because it is upper case. Neon requires a
-- strong password for SQL-created roles (60+ bits of entropy).
--
--   postgresql://AGENT_READ_WRITE_NON_GOLD:<password>@<host>/neondb?sslmode=require
CREATE ROLE "AGENT_READ_WRITE_NON_GOLD" LOGIN PASSWORD 'CHANGE_ME'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
GRANT CONNECT ON DATABASE neondb TO "AGENT_READ_WRITE_NON_GOLD";
GRANT USAGE ON SCHEMA public TO "AGENT_READ_WRITE_NON_GOLD";

-- Read everything, golden included, and every table the owner creates later.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "AGENT_READ_WRITE_NON_GOLD";
ALTER DEFAULT PRIVILEGES FOR ROLE neondb_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO "AGENT_READ_WRITE_NON_GOLD";

-- Add and correct rows in every non-golden table. A table created later is read-only to this role
-- until it is added here.
GRANT INSERT, UPDATE ON
  cache_lookup, conflicts,
  coordinate_recovery_attempts, coordinate_recovery_campaigns, coordinate_recovery_external_spend,
  coordinate_recovery_rows,
  dim_date, dim_facility, dim_field, dim_source,
  employee_feedback, facility, facility_duplicate_candidate, facility_event, facility_match_key,
  fact_assertions, fact_release_metrics,
  legacy_id_map, pipeline_run_events,
  ref_control, ref_known_gaps, ref_source_registry, ref_source_row,
  release_registry, web_research_submission
TO "AGENT_READ_WRITE_NON_GOLD";
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "AGENT_READ_WRITE_NON_GOLD";

-- Golden: golden_facility and golden_release are read-only. golden_dirty takes INSERT only, because
-- the golden_dirty_on_fact trigger on fact_assertions runs as the inserting role (not SECURITY
-- DEFINER); the golden refresh workflow, as the owner, drains it.
GRANT INSERT ON golden_dirty TO "AGENT_READ_WRITE_NON_GOLD";

ALTER ROLE "AGENT_READ_WRITE_NON_GOLD" SET statement_timeout = '60s';

-- Check, connected as AGENT_READ_WRITE_NON_GOLD:
--   SELECT rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = current_user;  -- f, f, f
--   SELECT pg_has_role(current_user, 'neon_superuser', 'MEMBER');                              -- f
-- Each of these must fail with "permission denied":
--   UPDATE golden_facility SET name = name WHERE false;
--   INSERT INTO golden_release SELECT * FROM golden_release WHERE false;
--   DELETE FROM fact_assertions WHERE false;
--   CREATE TABLE agent_probe (x int);
