"""Shared fixtures for the database tests.

The tests create their own database (incident_test) on whatever PostgreSQL server the
IV_TEST_PG_* variables point to, run the real init scripts from db/init/ against it, and
load a small seed. They never touch the application database.

Defaults match scripts/start_local_pg.sh. With docker compose, point them at the compose
database instead, for example:
    IV_TEST_PG_PORT=5432 IV_TEST_PG_ADMIN_PASSWORD=<POSTGRES_PASSWORD from .env> pytest
"""

import os
import pathlib
import subprocess

import psycopg
import pytest

from investigator import db
from tests import db_seed

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT_DIR = REPO_ROOT / "db" / "init"
TEST_DB = "incident_test"

TEST_ROLE_PASSWORDS = {
    "AGENT_RO_PASSWORD": "agent-test-pw",
    "SCORER_RO_PASSWORD": "scorer-test-pw",
    "GENERATOR_RW_PASSWORD": "generator-test-pw",
    "HARNESS_RW_PASSWORD": "harness-test-pw",
}

ROLES = ["agent_ro", "scorer_ro", "generator_rw", "harness_rw"]
SCHEMAS = ["evidence", "truth", "eval"]


def admin_settings():
    return {
        "host": os.environ.get("IV_TEST_PG_HOST", "127.0.0.1"),
        "port": os.environ.get("IV_TEST_PG_PORT", "54329"),
        "user": os.environ.get("IV_TEST_PG_ADMIN_USER", "postgres"),
        "password": os.environ.get("IV_TEST_PG_ADMIN_PASSWORD", "postgres-test-pw"),
    }


def admin_connect(dbname):
    settings = admin_settings()
    return psycopg.connect(
        host=settings["host"],
        port=settings["port"],
        user=settings["user"],
        password=settings["password"],
        dbname=dbname,
        autocommit=True,
    )


def run_init_scripts(env):
    """Run every file in db/init/ in name order, exactly as the postgres image would."""
    settings = admin_settings()
    for script in sorted(INIT_DIR.iterdir()):
        if script.suffix == ".sh":
            command = ["bash", str(script)]
        elif script.suffix == ".sql":
            command = [
                "psql", "-v", "ON_ERROR_STOP=1", "-q",
                "-U", settings["user"], "-d", TEST_DB, "-f", str(script),
            ]
        else:
            continue

        result = subprocess.run(command, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                "init script %s failed:\n%s\n%s" % (script.name, result.stdout, result.stderr)
            )


@pytest.fixture(scope="session")
def pg_database():
    """A freshly built incident_test database with the real schema and the seed data."""
    settings = admin_settings()

    try:
        maintenance = admin_connect("postgres")
    except psycopg.OperationalError as error:
        pytest.fail(
            "Cannot reach PostgreSQL at %s:%s as %s (%s).\n"
            "Start one with `bash scripts/start_local_pg.sh` (no Docker) or "
            "`docker compose up -d db`, and set IV_TEST_PG_* if it is not the default."
            % (settings["host"], settings["port"], settings["user"], error)
        )

    maintenance.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % TEST_DB)
    # Explicit UTF8 (from template0) so the tests do not depend on the server's defaults.
    maintenance.execute(
        "CREATE DATABASE %s TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
        % TEST_DB
    )
    maintenance.close()

    saved_environment = dict(os.environ)

    os.environ["IV_PG_HOST"] = settings["host"]
    os.environ["IV_PG_PORT"] = str(settings["port"])
    os.environ["IV_PG_DB"] = TEST_DB
    for name, value in TEST_ROLE_PASSWORDS.items():
        os.environ[name] = value

    script_env = dict(os.environ)
    script_env["PGHOST"] = settings["host"]
    script_env["PGPORT"] = str(settings["port"])
    script_env["PGPASSWORD"] = settings["password"]
    script_env["POSTGRES_USER"] = settings["user"]
    script_env["POSTGRES_DB"] = TEST_DB

    run_init_scripts(script_env)

    admin = admin_connect(TEST_DB)
    with admin.transaction():
        db_seed.seed_all(admin.cursor())
    admin.close()

    yield TEST_DB

    os.environ.clear()
    os.environ.update(saved_environment)

    cleanup = admin_connect("postgres")
    cleanup.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % TEST_DB)
    cleanup.close()


@pytest.fixture
def admin_conn(pg_database):
    """Superuser connection to the test database (autocommit, bypasses row-level security)."""
    connection = admin_connect(pg_database)
    yield connection
    connection.close()


@pytest.fixture
def connect_as(pg_database):
    """Open connections as one of the four application roles; closed after the test."""
    opened = []

    def _connect(role, autocommit=False):
        connection = psycopg.connect(db.conninfo_for(role), autocommit=autocommit)
        opened.append(connection)
        return connection

    yield _connect

    for connection in opened:
        connection.close()


@pytest.fixture(scope="module")
def pg1(pg_database):
    """PG-1 built and loaded as generator_rw for one test module, then deleted again, so
    modules that expect only the two seed incidents are unaffected."""
    from investigator.generator.build import build_incident
    from investigator.generator.load import delete_plan, load_incident
    from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan

    dataset = build_incident(find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1"))
    connection = psycopg.connect(db.conninfo_for("generator_rw"))
    load_incident(connection, dataset)
    yield dataset
    delete_plan(connection, "PG-1")
    connection.close()


def list_tables(admin_connection, schema, include_views=False):
    """Names of the tables (and optionally views) in a schema, read from the catalog."""
    kinds = ["r", "p"]
    if include_views:
        kinds.append("v")

    rows = admin_connection.execute(
        """
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relkind = ANY(%s)
         ORDER BY c.relname
        """,
        (schema, kinds),
    ).fetchall()

    names = []
    for row in rows:
        names.append(row[0])
    return names
