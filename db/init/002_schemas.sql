-- 002_schemas.sql - schemas, schema-level privileges, default privileges.
--
-- Runs as the bootstrap superuser. That same role creates every table in 003-005,
-- so the ALTER DEFAULT PRIVILEGES statements below (which have no FOR ROLE clause
-- and therefore apply to the current role) cover those tables.
-- 007_grants.sql repeats the grants explicitly after the tables exist, so the
-- result does not depend on who created what.

CREATE SCHEMA IF NOT EXISTS evidence;
CREATE SCHEMA IF NOT EXISTS truth;
CREATE SCHEMA IF NOT EXISTS eval;

-- Nobody gets anything by default.
REVOKE ALL ON SCHEMA public   FROM PUBLIC;
REVOKE ALL ON SCHEMA evidence FROM PUBLIC;
REVOKE ALL ON SCHEMA truth    FROM PUBLIC;
REVOKE ALL ON SCHEMA eval     FROM PUBLIC;

-- Only our four roles may connect, and none of them may create temp tables.
DO $$
BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
  EXECUTE format(
    'GRANT CONNECT ON DATABASE %I TO agent_ro, scorer_ro, generator_rw, harness_rw',
    current_database()
  );
END
$$;

-- Schema usage. agent_ro gets evidence and nothing else.
GRANT USAGE ON SCHEMA evidence TO agent_ro, scorer_ro, generator_rw;
GRANT USAGE ON SCHEMA truth    TO scorer_ro, generator_rw;
GRANT USAGE ON SCHEMA eval     TO scorer_ro, harness_rw;

-- evidence: agents and the scorer read; the generator loads.
ALTER DEFAULT PRIVILEGES IN SCHEMA evidence
  GRANT SELECT ON TABLES TO agent_ro, scorer_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA evidence
  GRANT SELECT, INSERT, DELETE ON TABLES TO generator_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA evidence
  GRANT USAGE, SELECT ON SEQUENCES TO generator_rw;

-- truth: only the scorer reads; the generator writes the answer key.
ALTER DEFAULT PRIVILEGES IN SCHEMA truth
  GRANT SELECT ON TABLES TO scorer_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA truth
  GRANT SELECT, INSERT, DELETE ON TABLES TO generator_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA truth
  GRANT USAGE, SELECT ON SEQUENCES TO generator_rw;

-- eval: the harness writes run logs and scores; the scorer reads.
ALTER DEFAULT PRIVILEGES IN SCHEMA eval
  GRANT SELECT ON TABLES TO scorer_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA eval
  GRANT SELECT, INSERT, UPDATE ON TABLES TO harness_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA eval
  GRANT USAGE, SELECT ON SEQUENCES TO harness_rw;
