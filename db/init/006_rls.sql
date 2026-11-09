-- 006_rls.sql - row-level security on EVERY table in the evidence schema.
--
-- The loop reads the catalog, so a table added to 003 later is covered without anyone
-- remembering to edit this file. (A test also fails if any evidence table lacks RLS or a
-- policy, in case a table is created after this script has run.)
--
--   agent_ro      sees only the incident named by the transaction-local setting
--                 app.incident_id. If the setting is missing, current_setting(..., true)
--                 returns NULL, the comparison is NULL, and no rows are visible.
--   scorer_ro     sees everything (it has to compare reports with the full evidence).
--   generator_rw  sees and writes everything (it builds the dataset).
--
-- harness_rw has no access to the evidence schema at all, so it has no policy.
-- Row-level security is bypassed by superusers, which only the bootstrap role is.

DO $$
DECLARE
  tbl record;
BEGIN
  FOR tbl IN
    SELECT c.relname AS table_name
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'evidence'
       AND c.relkind IN ('r', 'p')
  LOOP
    EXECUTE format('ALTER TABLE evidence.%I ENABLE ROW LEVEL SECURITY', tbl.table_name);

    EXECUTE format(
      'CREATE POLICY agent_scope ON evidence.%I FOR SELECT TO agent_ro
         USING (incident_id = current_setting(''app.incident_id'', true))',
      tbl.table_name
    );

    EXECUTE format(
      'CREATE POLICY scorer_all ON evidence.%I FOR SELECT TO scorer_ro USING (true)',
      tbl.table_name
    );

    EXECUTE format(
      'CREATE POLICY generator_all ON evidence.%I FOR ALL TO generator_rw
         USING (true) WITH CHECK (true)',
      tbl.table_name
    );
  END LOOP;
END
$$;
