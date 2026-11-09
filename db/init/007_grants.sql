-- 007_grants.sql - explicit grants after every table exists.
--
-- 002 set default privileges, which only apply to tables created afterwards by the same
-- role. This script states the intended result outright, so it holds even if a table was
-- created some other way. The permission tests compare the catalog against this matrix:
--
--                  evidence                    truth                       eval
--   agent_ro       SELECT (one incident)       -                           -
--   scorer_ro      SELECT                      SELECT                      SELECT
--   generator_rw   SELECT, INSERT, DELETE      SELECT, INSERT, DELETE      -
--   harness_rw     -                           -                           SELECT, INSERT, UPDATE

GRANT SELECT ON ALL TABLES IN SCHEMA evidence TO agent_ro, scorer_ro;
GRANT SELECT, INSERT, DELETE ON ALL TABLES IN SCHEMA evidence TO generator_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA evidence TO generator_rw;

GRANT SELECT ON ALL TABLES IN SCHEMA truth TO scorer_ro;
GRANT SELECT, INSERT, DELETE ON ALL TABLES IN SCHEMA truth TO generator_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA truth TO generator_rw;

GRANT SELECT ON ALL TABLES IN SCHEMA eval TO scorer_ro;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA eval TO harness_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA eval TO harness_rw;

-- Make the absences explicit too.
REVOKE ALL ON ALL TABLES IN SCHEMA truth    FROM agent_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA eval     FROM agent_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA evidence FROM harness_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA truth    FROM harness_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA eval     FROM generator_rw;
