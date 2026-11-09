"""Database helpers shared by the MCP server, the generator, the harness and the scorer.

Two rules live here because getting them wrong breaks the security model:

1. Every role connects with its own credentials (see db/init/001_roles.sh).
2. An agent connection is bound to ONE incident with a transaction-local setting.
   Never bind with a session-level setting: a pooled connection would carry the
   binding over to the next caller.
"""

import asyncio
import os
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import make_conninfo

ROLE_PASSWORD_ENV = {
    "agent_ro": "AGENT_RO_PASSWORD",
    "scorer_ro": "SCORER_RO_PASSWORD",
    "generator_rw": "GENERATOR_RW_PASSWORD",
    "harness_rw": "HARNESS_RW_PASSWORD",
}


def conninfo_for(role):
    """Build a libpq connection string for one of the four application roles."""
    if role not in ROLE_PASSWORD_ENV:
        raise ValueError("unknown database role: " + str(role))

    host = os.environ.get("IV_PG_HOST", "localhost")
    port = os.environ.get("IV_PG_PORT", "5432")
    dbname = os.environ.get("IV_PG_DB", "incidents")
    password = os.environ.get(ROLE_PASSWORD_ENV[role], "")

    return make_conninfo(
        host=host,
        port=port,
        dbname=dbname,
        user=role,
        password=password,
    )


def bind_incident(cursor, incident_id):
    """Bind the current transaction to one incident (row-level security reads this).

    The third argument of set_config is TRUE, which makes the value
    transaction-local: it disappears at COMMIT or ROLLBACK, so a connection
    returned to a pool cannot leak the binding.

    This must run inside an open transaction. In autocommit mode every statement
    is its own transaction and the binding would vanish immediately, so we
    refuse to continue instead of silently returning no rows later.
    """
    cursor.execute("SELECT set_config('app.incident_id', %s, true)", (incident_id,))

    status = cursor.connection.info.transaction_status
    if status == psycopg.pq.TransactionStatus.IDLE:
        raise RuntimeError(
            "bind_incident() was called outside a transaction; "
            "the binding is already gone. Use incident_transaction()."
        )


class ThreadedConnection:
    """An async face over a plain (sync) psycopg connection: each call runs in a worker thread.

    Why: on Windows, subprocesses (the MCP server, the claude CLI) need asyncio's
    ProactorEventLoop, and async psycopg refuses to run on it. The runner therefore talks to
    Postgres through this wrapper on every platform. psycopg connections serialise
    concurrent use internally, so sharing one between threads is safe.
    """

    def __init__(self, connection):
        self.connection = connection

    async def execute(self, sql, params=None):
        await asyncio.to_thread(self.connection.execute, sql, params)

    async def close(self):
        await asyncio.to_thread(self.connection.close)


@contextmanager
def incident_transaction(connection, incident_id):
    """Open a transaction, bind it to the incident, and yield a cursor."""
    with connection.transaction():
        cursor = connection.cursor()
        bind_incident(cursor, incident_id)
        yield cursor
