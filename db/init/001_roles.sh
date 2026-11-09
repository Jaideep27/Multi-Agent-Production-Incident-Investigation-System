#!/bin/bash
# 001_roles.sh - create the four application roles.
#
# Why a shell script: role passwords come from environment variables, and SQL files
# cannot read them. The official postgres image runs this once on first start; the
# test suite runs it too, with PGHOST/PGPORT/PGPASSWORD set.
#
# Safe to run more than once: roles are created only if missing, passwords are
# always reset.

set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${AGENT_RO_PASSWORD:?AGENT_RO_PASSWORD must be set}"
: "${SCORER_RO_PASSWORD:?SCORER_RO_PASSWORD must be set}"
: "${GENERATOR_RW_PASSWORD:?GENERATOR_RW_PASSWORD must be set}"
: "${HARNESS_RW_PASSWORD:?HARNESS_RW_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 -q \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v agent_pw="$AGENT_RO_PASSWORD" \
  -v scorer_pw="$SCORER_RO_PASSWORD" \
  -v generator_pw="$GENERATOR_RW_PASSWORD" \
  -v harness_pw="$HARNESS_RW_PASSWORD" <<'EOSQL'

-- None of these roles may be a superuser, bypass row-level security, create roles
-- or databases, or replicate. They also own nothing and belong to no other role.
DO $$
DECLARE
  role_name text;
BEGIN
  FOREACH role_name IN ARRAY ARRAY['agent_ro', 'scorer_ro', 'generator_rw', 'harness_rw']
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format(
        'CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',
        role_name
      );
    END IF;
  END LOOP;
END
$$;

ALTER ROLE agent_ro      PASSWORD :'agent_pw';
ALTER ROLE scorer_ro     PASSWORD :'scorer_pw';
ALTER ROLE generator_rw  PASSWORD :'generator_pw';
ALTER ROLE harness_rw    PASSWORD :'harness_pw';

-- Extra safety for the role the MCP server uses. These are role defaults, not a
-- security boundary on their own (a session can SET them back); the real
-- boundaries are the grants and row-level security in the later scripts.
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '10s';
ALTER ROLE agent_ro SET idle_in_transaction_session_timeout = '30s';

EOSQL
