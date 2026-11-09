#!/bin/bash
# Stop the throwaway local PostgreSQL cluster started by start_local_pg.sh.

set -euo pipefail

PGBIN="${PGBIN:-/usr/lib/postgresql/16/bin}"
DATADIR="${IV_TEST_PGDATA:-/var/lib/postgresql/iv_test_cluster}"

if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u postgres -- env PGBIN="$PGBIN" IV_TEST_PGDATA="$DATADIR" bash "$0"
fi

if "$PGBIN/pg_ctl" -D "$DATADIR" status >/dev/null 2>&1; then
  "$PGBIN/pg_ctl" -D "$DATADIR" -m fast -w stop
else
  echo "local test cluster is not running"
fi
