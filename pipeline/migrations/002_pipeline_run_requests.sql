-- Applied by `python -m pipeline.warehouse init` after the base schema, like 001.
-- The ADL_Viz run controls' dispatch guard (src/lib/pipeline-runs.ts, dispatch()): one row per
-- "start a run" request, so two simultaneous requests cannot both dispatch. The site creates the
-- table itself with this exact statement; keep the two identical.
CREATE TABLE IF NOT EXISTS pipeline_run_requests (
      request_id TEXT PRIMARY KEY, kind TEXT NOT NULL, employee_name TEXT NOT NULL,
      state TEXT NOT NULL, run_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
