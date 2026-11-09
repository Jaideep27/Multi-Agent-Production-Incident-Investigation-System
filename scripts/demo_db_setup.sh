#!/bin/bash
# demo_db_setup.sh - create a persistent "incidents" database (if missing) with the real init
# scripts, then build and load incidents from the plan. Runs inside the iv-test-runner
# container (see scripts/demo_db_in_docker.ps1), which has bash and psql.
#
# Uses the local test role passwords: this database is for local demo runs only.
set -euo pipefail

: "${PGHOST:?PGHOST must be set}"
: "${PGPASSWORD:?PGPASSWORD must be set}"
DB="${IV_PG_DB:-incidents}"
export PGPORT="${PGPORT:-5432}"
export POSTGRES_USER=postgres
export POSTGRES_DB="$DB"

exists=$(psql -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'")
if [ "$exists" != "1" ]; then
  echo "creating database $DB"
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
    -c "CREATE DATABASE $DB TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
  for script in db/init/*; do
    case "$script" in
      *.sh)  bash "$script" ;;
      *.sql) psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 -q -f "$script" ;;
    esac
  done
else
  echo "database $DB already exists; schema left as is"
fi

IV_PG_HOST="$PGHOST" IV_PG_PORT="$PGPORT" IV_PG_DB="$DB" python -m investigator.generator "$@"
