"""Role and privilege tests. Everything is read from the catalog, so a table added later is
checked without anyone editing this file."""

import psycopg
import pytest

from tests.conftest import ROLES, SCHEMAS, list_tables

pytestmark = pytest.mark.db

TABLE_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]

# The intended privilege matrix (see db/init/007_grants.sql).
EXPECTED = {
    "evidence": {
        "agent_ro": {"SELECT"},
        "scorer_ro": {"SELECT"},
        "generator_rw": {"SELECT", "INSERT", "DELETE"},
        "harness_rw": set(),
    },
    "truth": {
        "agent_ro": set(),
        "scorer_ro": {"SELECT"},
        "generator_rw": {"SELECT", "INSERT", "DELETE"},
        "harness_rw": set(),
    },
    "eval": {
        "agent_ro": set(),
        "scorer_ro": {"SELECT"},
        "generator_rw": set(),
        "harness_rw": {"SELECT", "INSERT", "UPDATE"},
    },
}


def granted_privileges(admin_conn, role, schema, table):
    granted = set()
    for privilege in TABLE_PRIVILEGES:
        row = admin_conn.execute(
            "SELECT has_table_privilege(%s, %s, %s)",
            (role, schema + "." + table, privilege),
        ).fetchone()
        if row[0]:
            granted.add(privilege)
    return granted


# ----------------------------------------------------------------- role attributes


@pytest.mark.parametrize("role", ROLES)
def test_roles_have_no_dangerous_attributes(admin_conn, role):
    row = admin_conn.execute(
        """
        SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, rolcanlogin
          FROM pg_roles WHERE rolname = %s
        """,
        (role,),
    ).fetchone()

    assert row is not None, role + " does not exist"
    superuser, bypass_rls, create_role, create_db, replication, can_login = row
    assert superuser is False
    assert bypass_rls is False
    assert create_role is False
    assert create_db is False
    assert replication is False
    assert can_login is True


@pytest.mark.parametrize("role", ROLES)
def test_roles_belong_to_no_other_role(admin_conn, role):
    row = admin_conn.execute(
        "SELECT count(*) FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname = %s)",
        (role,),
    ).fetchone()
    assert row[0] == 0


@pytest.mark.parametrize("role", ROLES)
def test_roles_own_no_relations(admin_conn, role):
    # An owner can read its own table regardless of grants and (without FORCE) bypasses
    # row-level security, so no application role may own anything.
    row = admin_conn.execute(
        "SELECT count(*) FROM pg_class WHERE relowner = (SELECT oid FROM pg_roles WHERE rolname = %s)",
        (role,),
    ).fetchone()
    assert row[0] == 0


def test_agent_role_defaults_to_read_only_with_timeouts(connect_as):
    connection = connect_as("agent_ro", autocommit=True)
    # current_setting() returns text; SHOW comes back as raw bytes through psycopg.
    read_only = connection.execute("SELECT current_setting('default_transaction_read_only')").fetchone()[0]
    statement_timeout = connection.execute("SELECT current_setting('statement_timeout')").fetchone()[0]
    assert read_only == "on"
    assert statement_timeout == "10s"


# ------------------------------------------------------------- privilege matrix


def test_privilege_matrix_matches_catalog(admin_conn):
    mismatches = []

    for schema in SCHEMAS:
        tables = list_tables(admin_conn, schema, include_views=True)
        assert tables, "no tables found in schema " + schema

        for table in tables:
            for role in ROLES:
                expected = EXPECTED[schema][role]
                actual = granted_privileges(admin_conn, role, schema, table)
                if actual != expected:
                    mismatches.append(
                        "%s.%s role=%s expected=%s actual=%s"
                        % (schema, table, role, sorted(expected), sorted(actual))
                    )

    assert mismatches == []


def test_public_pseudo_role_has_nothing(admin_conn):
    for schema in SCHEMAS + ["public"]:
        for privilege in ["USAGE", "CREATE"]:
            row = admin_conn.execute(
                "SELECT has_schema_privilege('public', %s, %s)", (schema, privilege)
            ).fetchone()
            assert row[0] is False, "PUBLIC has %s on schema %s" % (privilege, schema)

    for privilege in ["CONNECT", "TEMPORARY", "CREATE"]:
        row = admin_conn.execute(
            "SELECT has_database_privilege('public', current_database(), %s)", (privilege,)
        ).fetchone()
        assert row[0] is False, "PUBLIC has %s on the database" % privilege


def test_schema_usage_matrix(admin_conn):
    expected_usage = {
        "evidence": {"agent_ro", "scorer_ro", "generator_rw"},
        "truth": {"scorer_ro", "generator_rw"},
        "eval": {"scorer_ro", "harness_rw"},
    }
    for schema in SCHEMAS:
        for role in ROLES:
            row = admin_conn.execute(
                "SELECT has_schema_privilege(%s, %s, 'USAGE')", (role, schema)
            ).fetchone()
            assert row[0] == (role in expected_usage[schema]), (role, schema)


def test_default_privileges_apply_to_tables_created_later(admin_conn):
    """Correction #1: default privileges must work, not only the explicit grants."""
    probes = {
        "evidence": ("evidence.zz_default_priv_probe", {"agent_ro": {"SELECT"}, "generator_rw": {"INSERT"}}),
        "truth": ("truth.zz_default_priv_probe", {"scorer_ro": {"SELECT"}, "generator_rw": {"INSERT"}}),
        "eval": ("eval.zz_default_priv_probe", {"scorer_ro": {"SELECT"}, "harness_rw": {"INSERT", "UPDATE"}}),
    }
    forbidden = {
        "evidence": [("agent_ro", "INSERT"), ("harness_rw", "SELECT")],
        "truth": [("agent_ro", "SELECT"), ("harness_rw", "SELECT")],
        "eval": [("agent_ro", "SELECT"), ("generator_rw", "SELECT")],
    }

    try:
        for schema, (table_name, allowed) in probes.items():
            admin_conn.execute("CREATE TABLE %s (incident_id text)" % table_name)

            for role, privileges in allowed.items():
                for privilege in privileges:
                    row = admin_conn.execute(
                        "SELECT has_table_privilege(%s, %s, %s)", (role, table_name, privilege)
                    ).fetchone()
                    assert row[0] is True, (role, privilege, table_name)

            for role, privilege in forbidden[schema]:
                row = admin_conn.execute(
                    "SELECT has_table_privilege(%s, %s, %s)", (role, table_name, privilege)
                ).fetchone()
                assert row[0] is False, (role, privilege, table_name)
    finally:
        for schema, (table_name, allowed) in probes.items():
            admin_conn.execute("DROP TABLE IF EXISTS %s" % table_name)


# ------------------------------------------------ what agent_ro can and cannot do


def test_agent_denied_on_truth_and_eval(admin_conn, connect_as):
    connection = connect_as("agent_ro", autocommit=True)

    for schema in ["truth", "eval"]:
        for table in list_tables(admin_conn, schema, include_views=True):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute("SELECT * FROM %s.%s" % (schema, table))


def test_agent_cannot_write_evidence(admin_conn, connect_as):
    connection = connect_as("agent_ro", autocommit=True)
    # The role is read-only by default; switch that off so we test privileges, not the setting.
    connection.execute("SET default_transaction_read_only = off")

    for table in list_tables(admin_conn, "evidence"):
        full_name = "evidence." + table

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("INSERT INTO %s DEFAULT VALUES" % full_name)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("UPDATE %s SET incident_id = incident_id" % full_name)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("DELETE FROM %s" % full_name)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("TRUNCATE %s" % full_name)


def test_agent_cannot_create_objects(connect_as):
    connection = connect_as("agent_ro", autocommit=True)
    connection.execute("SET default_transaction_read_only = off")

    statements = [
        "CREATE TABLE evidence.zz_agent_table (x int)",
        "CREATE TABLE public.zz_agent_table (x int)",
        "CREATE TEMP TABLE zz_agent_temp (x int)",
        "CREATE SCHEMA zz_agent_schema",
        "CREATE ROLE zz_agent_role",
    ]
    for statement in statements:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(statement)


def test_agent_cannot_switch_to_a_more_powerful_role(connect_as):
    connection = connect_as("agent_ro", autocommit=True)
    for role in ["postgres", "scorer_ro", "generator_rw", "harness_rw"]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SET ROLE %s" % role)


# ------------------------------------------------ the other roles do their jobs


def test_generator_can_load_evidence_and_truth(connect_as):
    """Also proves the sequence grants work (services.service_id is a bigserial)."""
    connection = connect_as("generator_rw")
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO evidence.incidents
          (incident_id, domain, reported_at, window_start, window_end, summary, reporter)
        VALUES ('inc_cccccccc', 'ott', now(), now() - interval '1 hour', now(), 's', 'r')
        """
    )
    cursor.execute(
        "INSERT INTO evidence.services (incident_id, name, role) VALUES ('inc_cccccccc', 'x', 'app') "
        "RETURNING service_id"
    )
    assert cursor.fetchone()[0] is not None

    cursor.execute(
        """
        INSERT INTO truth.scenarios
          (incident_id, plan_id, archetype, seed, generator_version, expected_outcome,
           solvable_after_noise, root_component, root_category, mechanism)
        VALUES ('inc_cccccccc', 'TMP-1', 'a', 1, 't', 'identifiable', true, 'x', 'bad_deploy', 'm')
        """
    )
    cursor.execute("INSERT INTO truth.noise_applied (incident_id, noise_type) VALUES ('inc_cccccccc', 'n')")

    connection.rollback()


def test_generator_cannot_read_eval(connect_as):
    connection = connect_as("generator_rw", autocommit=True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("SELECT * FROM eval.runs")


def test_harness_can_write_eval_but_cannot_read_evidence_or_truth(connect_as):
    connection = connect_as("harness_rw", autocommit=True)

    with connection.transaction(force_rollback=True):
        row = connection.execute(
            "INSERT INTO eval.runs (incident_id, system, model, status) "
            "VALUES ('inc_aaaaaaaa', 'single_agent', 'haiku', 'running') RETURNING run_id"
        ).fetchone()
        assert row[0] is not None

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("SELECT * FROM evidence.incidents")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        connection.execute("SELECT * FROM truth.scenarios")
