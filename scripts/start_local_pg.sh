#!/bin/bash
# Start a throwaway local PostgreSQL 16 cluster for running the tests when Docker is not
# available. NOT for real use: fixed test password, data directory is disposable.
#
#   bash scripts/start_local_pg.sh
#
# Then run the tests with the defaults, which match this script
# (host 127.0.0.1, port 54329, user postgres, password postgres-test-pw).

set -euo pipefail

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
DATADIR="${IV_TEST_PGDATA:-/var/lib/postgresql/iv_test_cluster}"
PORT="${IV_TEST_PG_PORT:-54329}"
ADMIN_PASSWORD="${IV_TEST_PG_ADMIN_PASSWORD:-postgres-test-pw}"

# PostgreSQL refuses to run as root, so hand over to the postgres OS user.
if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u postgres -- env \
    PGBIN="$PGBIN" IV_TEST_PGDATA="$DATADIR" IV_TEST_PG_PORT="$PORT" \
    IV_TEST_PG_ADMIN_PASSWORD="$ADMIN_PASSWORD" bash "$0"
fi

if [ ! -d "$DATADIR" ]; then
  PWFILE="$(mktemp)"
  printf '%s' "$ADMIN_PASSWORD" > "$PWFILE"
  # UTF8 matters: with a SQL_ASCII cluster, psycopg hands text back as bytes.
  "$PGBIN/initdb" -D "$DATADIR" -U postgres -E UTF8 --locale=C.UTF-8 \
    --auth-local=trust --auth-host=scram-sha-256 --pwfile="$PWFILE" >/dev/null
  rm -f "$PWFILE"
fi

if "$PGBIN/pg_ctl" -D "$DATADIR" status >/dev/null 2>&1; then
  echo "local test cluster already running on port $PORT"
  exit 0
fi

"$PGBIN/pg_ctl" -D "$DATADIR" -l "$DATADIR/server.log" -w start \
  -o "-p $PORT -c listen_addresses=127.0.0.1 -c max_connections=100"

echo "local test cluster running on 127.0.0.1:$PORT"
