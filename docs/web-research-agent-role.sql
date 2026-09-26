-- A login for the external web-research agent: it can read golden and write submissions, nothing else.
-- NOT a migration (warehouse init does not run it): the owner runs it once, by hand.
-- Run once as the owner (Neon SQL editor or psql). Replace the password; hand the agent only the
-- connection string of THIS role, never the owner's.
--
--   postgresql://web_research_agent:<password>@<host>/neondb?sslmode=require
CREATE ROLE web_research_agent LOGIN PASSWORD 'CHANGE_ME';
GRANT CONNECT ON DATABASE neondb TO web_research_agent;
GRANT USAGE ON SCHEMA public TO web_research_agent;
GRANT SELECT ON golden_facility, facility, dim_field, facility_duplicate_candidate TO web_research_agent;
GRANT SELECT, INSERT ON web_research_submission TO web_research_agent;
-- Re-submitting a draft before it is ingested (ON CONFLICT ... DO UPDATE ... WHERE status = 'pending')
GRANT UPDATE (payload, submitted_at) ON web_research_submission TO web_research_agent;
ALTER ROLE web_research_agent SET statement_timeout = '60s';
