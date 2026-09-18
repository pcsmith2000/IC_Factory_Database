-- Applied by `python -m pipeline.warehouse init` after the base schema. No sample submissions.
CREATE TABLE IF NOT EXISTS employee_feedback (
  feedback_id TEXT PRIMARY KEY,
  facility_key TEXT NOT NULL,
  employee_name TEXT NOT NULL,
  auth_method TEXT NOT NULL DEFAULT 'shared_passcode',
  channel TEXT NOT NULL CHECK (channel IN ('chat','field_edit')),
  note TEXT NOT NULL,
  changes_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  creates_facility INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_employee_feedback_facility ON employee_feedback(facility_key);
INSERT INTO dim_source (source_key,source_id,name,class,method,status_basis,status)
VALUES ('adl_employee_feedback','adl_employee_feedback','ADL employee feedback','human_feedback','employee_chat_or_field_edit','human_verified','active')
ON CONFLICT (source_key) DO UPDATE SET name=excluded.name, class=excluded.class;

-- Keep the assertion -> golden relationship intact even when older enrichment jobs
-- replace golden from a snapshot taken before an employee saved a correction.
CREATE OR REPLACE FUNCTION preserve_employee_feedback() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a RECORD; patch JSONB := '{}'::jsonb;
BEGIN
  FOR a IN
    SELECT DISTINCT ON (f.field_key) f.*
    FROM fact_assertions f JOIN employee_feedback e ON e.feedback_id=f.row_hash
    WHERE f.facility_key=NEW.facility_key AND f.source_key='adl_employee_feedback'
    ORDER BY f.field_key, e.created_at DESC, e.feedback_id DESC, f.release_tag DESC
  LOOP
    -- Only columns with source lineage are mutable facts.
    IF to_jsonb(NEW) ? (a.field_key || '__source') THEN
      patch := patch || jsonb_build_object(a.field_key,a.value,a.field_key || '__source','adl_employee_feedback');
    END IF;
  END LOOP;
  IF patch <> '{}'::jsonb THEN
    -- Carry the complete employee assertion history, not just today's winners.
    INSERT INTO fact_assertions
      (assertion_id,release_tag,facility_key,source_key,field_key,date_key,value,basis,site_visit,row_hash,confidence,source_class,asserted_at)
    SELECT DISTINCT f.assertion_id,NEW.release_tag,f.facility_key,f.source_key,f.field_key,f.date_key,
      f.value,f.basis,f.site_visit,f.row_hash,f.confidence,f.source_class,f.asserted_at
    FROM fact_assertions f JOIN employee_feedback e ON e.feedback_id=f.row_hash
    WHERE f.facility_key=NEW.facility_key AND f.source_key='adl_employee_feedback'
    ON CONFLICT (assertion_id,release_tag) DO NOTHING;
    NEW := jsonb_populate_record(NEW, patch);
    SELECT count(*),count(DISTINCT source_key) INTO NEW.n_assertions,NEW.n_sources
      FROM fact_assertions WHERE facility_key=NEW.facility_key AND release_tag=NEW.release_tag;
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS golden_employee_feedback ON golden_facility;
CREATE TRIGGER golden_employee_feedback BEFORE INSERT OR UPDATE ON golden_facility
FOR EACH ROW EXECUTE FUNCTION preserve_employee_feedback();

-- One atomic promotion, including locks, for the Neon HTTP client. Older releases
-- cannot overwrite a newer release. Employee-created facilities not in a stale
-- snapshot are retained; the row trigger reapplies all employee corrections.
CREATE OR REPLACE FUNCTION replace_golden_with_feedback(payload JSONB, expected_release TEXT)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE columns_sql TEXT; updates_sql TEXT; written INTEGER;
BEGIN
  PERFORM pg_advisory_xact_lock(7419026);
  LOCK TABLE golden_facility IN SHARE ROW EXCLUSIVE MODE;
  IF EXISTS (SELECT 1 FROM golden_facility WHERE release_tag <> expected_release) THEN
    RAISE EXCEPTION 'Release changed during promotion';
  END IF;
  SELECT string_agg(quote_ident(attname),',' ORDER BY attnum),
    string_agg(format('%I=EXCLUDED.%I',attname,attname),',' ORDER BY attnum)
    INTO columns_sql,updates_sql
    FROM pg_attribute WHERE attrelid='golden_facility'::regclass AND attnum>0 AND NOT attisdropped;
  DELETE FROM golden_facility g WHERE NOT EXISTS
    (SELECT 1 FROM employee_feedback e WHERE e.facility_key=g.facility_key AND e.creates_facility=1);
  EXECUTE format('INSERT INTO golden_facility (%s) SELECT %s FROM jsonb_populate_recordset(NULL::golden_facility,$1) ON CONFLICT(facility_key) DO UPDATE SET %s',columns_sql,columns_sql,updates_sql)
    USING payload;
  GET DIAGNOSTICS written = ROW_COUNT;
  RETURN written;
END;
$$;
