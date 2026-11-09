"""Row-level security tests. Agents must never see another incident, and an unbound session
must see nothing at all."""

import threading

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from investigator import db
from tests.conftest import list_tables
from tests.db_seed import INCIDENT_A, INCIDENT_B

pytestmark = pytest.mark.db


def counts_by_incident(connection, table):
    """{incident_id: row count} for one evidence table, as seen by this connection."""
    rows = connection.execute(
        "SELECT incident_id, count(*) FROM evidence.%s GROUP BY incident_id" % table
    ).fetchall()
    counts = {}
    for incident_id, count in rows:
        counts[incident_id] = count
    return counts


# --------------------------------------------------------- structure (from catalog)


def test_every_evidence_table_has_rls_enabled(admin_conn):
    tables = list_tables(admin_conn, "evidence")
    assert len(tables) >= 11

    missing = []
    for table in tables:
        row = admin_conn.execute(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE oid = ('evidence.' || %s)::regclass",
            (table,),
        ).fetchone()
        if row[0] is not True:
            missing.append(table)

    assert missing == []


def test_every_evidence_table_has_a_policy_for_each_reading_role(admin_conn):
    problems = []

    for table in list_tables(admin_conn, "evidence"):
        for role in ["agent_ro", "scorer_ro", "generator_rw"]:
            row = admin_conn.execute(
                """
                SELECT count(*) FROM pg_policies
                 WHERE schemaname = 'evidence' AND tablename = %s
                   AND %s = ANY(roles) AND cmd IN ('SELECT', 'ALL')
                """,
                (table, role),
            ).fetchone()
            if row[0] == 0:
                problems.append("%s has no policy for %s" % (table, role))

    assert problems == []


def test_every_evidence_table_has_an_incident_id_column(admin_conn):
    missing = []
    for table in list_tables(admin_conn, "evidence"):
        row = admin_conn.execute(
            """
            SELECT count(*) FROM information_schema.columns
             WHERE table_schema = 'evidence' AND table_name = %s
               AND column_name = 'incident_id' AND data_type = 'text'
            """,
            (table,),
        ).fetchone()
        if row[0] != 1:
            missing.append(table)

    assert missing == []


def test_agent_policies_are_scoped_to_the_incident_binding(admin_conn):
    for table in list_tables(admin_conn, "evidence"):
        row = admin_conn.execute(
            "SELECT qual FROM pg_policies "
            "WHERE schemaname = 'evidence' AND tablename = %s AND policyname = 'agent_scope'",
            (table,),
        ).fetchone()
        assert row is not None, table
        assert "app.incident_id" in row[0], table
        assert "incident_id" in row[0], table


def test_seed_covers_every_evidence_table_for_both_incidents(admin_conn):
    """Guards against vacuous tests: every table has rows for A and for B."""
    for table in list_tables(admin_conn, "evidence"):
        counts = counts_by_incident(admin_conn, table)
        assert counts.get(INCIDENT_A, 0) > 0, table + " has no seed rows for incident A"
        assert counts.get(INCIDENT_B, 0) > 0, table + " has no seed rows for incident B"


# ----------------------------------------------------------------- behaviour


def test_unbound_agent_sees_nothing_in_any_table(admin_conn, connect_as):
    connection = connect_as("agent_ro", autocommit=True)

    for table in list_tables(admin_conn, "evidence"):
        row = connection.execute("SELECT count(*) FROM evidence.%s" % table).fetchone()
        assert row[0] == 0, table + " is visible without an incident binding"


@pytest.mark.parametrize("own,other", [(INCIDENT_A, INCIDENT_B), (INCIDENT_B, INCIDENT_A)])
def test_bound_agent_sees_only_its_own_incident(admin_conn, connect_as, own, other):
    connection = connect_as("agent_ro")

    for table in list_tables(admin_conn, "evidence"):
        expected_own = counts_by_incident(admin_conn, table)[own]

        with db.incident_transaction(connection, own):
            seen = counts_by_incident(connection, table)

        assert seen == {own: expected_own}, "%s leaked rows: %s" % (table, seen)
        assert other not in seen


def test_filterless_select_star_returns_only_the_bound_incident(admin_conn, connect_as):
    """The 'buggy tool' test: a query that forgot its WHERE clause is still contained."""
    connection = connect_as("agent_ro")

    for table in list_tables(admin_conn, "evidence"):
        with db.incident_transaction(connection, INCIDENT_A) as cursor:
            cursor.execute("SELECT incident_id FROM evidence.%s" % table)
            rows = cursor.fetchall()

        assert len(rows) > 0, table
        for row in rows:
            assert row[0] == INCIDENT_A, table


@pytest.mark.parametrize("binding", ["inc_zzzzzzzz", "", "inc_", "%", "inc_%", "inc_aaaaaaaa "])
def test_unknown_or_malformed_bindings_see_nothing(admin_conn, connect_as, binding):
    connection = connect_as("agent_ro")

    for table in list_tables(admin_conn, "evidence"):
        with db.incident_transaction(connection, binding):
            row = connection.execute("SELECT count(*) FROM evidence.%s" % table).fetchone()
        assert row[0] == 0, "%s visible with binding %r" % (table, binding)


INJECTION_STRINGS = [
    "inc_aaaaaaaa' OR '1'='1",
    "inc_aaaaaaaa'; SET app.incident_id = 'inc_bbbbbbbb",
    "'; SELECT set_config('app.incident_id', 'inc_bbbbbbbb', false); --",
    "inc_aaaaaaaa\\'; DROP SCHEMA truth CASCADE; --",
]


def test_injection_strings_in_the_binding_do_nothing(connect_as):
    connection = connect_as("agent_ro")

    for text in INJECTION_STRINGS:
        with db.incident_transaction(connection, text):
            row = connection.execute("SELECT count(*) FROM evidence.log_entries").fetchone()
        assert row[0] == 0, text

    # After all of that, a legitimate binding still sees exactly its own incident.
    with db.incident_transaction(connection, INCIDENT_A):
        incident_ids = connection.execute("SELECT DISTINCT incident_id FROM evidence.log_entries").fetchall()
    assert incident_ids == [(INCIDENT_A,)]


def test_binding_outside_a_transaction_is_refused(connect_as):
    connection = connect_as("agent_ro", autocommit=True)
    cursor = connection.cursor()
    with pytest.raises(RuntimeError):
        db.bind_incident(cursor, INCIDENT_A)


def test_binding_does_not_survive_on_a_reused_pooled_connection(pg_database):
    """Correction #5: pool of one connection; the binding must not leak to the next caller."""
    pool = ConnectionPool(db.conninfo_for("agent_ro"), min_size=1, max_size=1, open=True)
    try:
        with pool.connection() as first:
            with db.incident_transaction(first, INCIDENT_A):
                pid_first = first.execute("SELECT pg_backend_pid()").fetchone()[0]
                bound_rows = first.execute("SELECT count(*) FROM evidence.log_entries").fetchone()[0]
        assert bound_rows > 0

        with pool.connection() as second:
            pid_second = second.execute("SELECT pg_backend_pid()").fetchone()[0]
            leaked_rows = second.execute("SELECT count(*) FROM evidence.log_entries").fetchone()[0]

        assert pid_second == pid_first, "the pool did not reuse the connection, test is vacuous"
        assert leaked_rows == 0
    finally:
        pool.close()


def test_two_servers_bound_to_different_incidents_see_disjoint_data(pg_database):
    barrier = threading.Barrier(2, timeout=20)
    results = {}
    errors = []

    def worker(name, incident_id):
        try:
            connection = psycopg.connect(db.conninfo_for("agent_ro"))
            try:
                with db.incident_transaction(connection, incident_id) as cursor:
                    # Both transactions are open at the same moment before anyone reads.
                    barrier.wait()
                    cursor.execute("SELECT evidence_id, incident_id FROM evidence.log_entries")
                    results[name] = cursor.fetchall()
            finally:
                connection.close()
        except Exception as error:
            errors.append(error)

    thread_a = threading.Thread(target=worker, args=("a", INCIDENT_A))
    thread_b = threading.Thread(target=worker, args=("b", INCIDENT_B))
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()

    assert errors == []

    ids_a = set()
    for evidence_id, incident_id in results["a"]:
        assert incident_id == INCIDENT_A
        ids_a.add(evidence_id)

    ids_b = set()
    for evidence_id, incident_id in results["b"]:
        assert incident_id == INCIDENT_B
        ids_b.add(evidence_id)

    assert ids_a and ids_b
    assert ids_a.isdisjoint(ids_b)


def test_scorer_sees_everything(admin_conn, connect_as):
    connection = connect_as("scorer_ro", autocommit=True)

    for table in list_tables(admin_conn, "evidence"):
        assert counts_by_incident(connection, table) == counts_by_incident(admin_conn, table), table

    for schema in ["truth", "eval"]:
        for table in list_tables(admin_conn, schema, include_views=False):
            seen = connection.execute("SELECT count(*) FROM %s.%s" % (schema, table)).fetchone()[0]
            expected = admin_conn.execute("SELECT count(*) FROM %s.%s" % (schema, table)).fetchone()[0]
            assert seen == expected, schema + "." + table
            assert seen > 0, schema + "." + table + " is empty in the seed"


def test_generator_sees_all_evidence_rows(admin_conn, connect_as):
    connection = connect_as("generator_rw", autocommit=True)
    for table in list_tables(admin_conn, "evidence"):
        assert counts_by_incident(connection, table) == counts_by_incident(admin_conn, table), table
