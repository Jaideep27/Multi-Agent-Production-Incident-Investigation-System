"""Relational integrity: composite foreign keys, CHECK constraints, cascades.

These run as the superuser, which bypasses row-level security but not constraints. Each
case runs inside a transaction that is always rolled back, so the seed stays untouched.
"""

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests import db_seed
from tests.conftest import list_tables
from tests.db_seed import INCIDENT_A, INCIDENT_B

pytestmark = pytest.mark.db


def service_id(admin_conn, incident_id, name):
    row = admin_conn.execute(
        "SELECT service_id FROM evidence.services WHERE incident_id = %s AND name = %s",
        (incident_id, name),
    ).fetchone()
    return row[0]


def expect_failure(admin_conn, error_class, statements):
    """Run statements in one transaction that is always rolled back; the last must fail."""
    with pytest.raises(error_class):
        with admin_conn.transaction(force_rollback=True):
            for sql, params in statements:
                admin_conn.execute(sql, params)


def new_incident_sql(incident_id):
    return (
        "INSERT INTO evidence.incidents "
        "(incident_id, domain, reported_at, window_start, window_end, summary, reporter) "
        "VALUES (%s, 'ott', now(), now() - interval '1 hour', now(), 's', 'r')",
        (incident_id,),
    )


# ------------------------------------------------------- composite foreign keys


def test_metric_point_cannot_point_at_another_incidents_series(admin_conn):
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        (
            "INSERT INTO evidence.metric_points (incident_id, series_id, ts, value) "
            "VALUES (%s, 'M-a-1', now(), 1.0)",
            (INCIDENT_B,),
        ),
    ])


def test_change_detail_cannot_point_at_another_incidents_change(admin_conn):
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        (
            "INSERT INTO evidence.change_details (evidence_id, incident_id, diff) "
            "VALUES ('C-a-2', %s, %s)",
            (INCIDENT_B, Jsonb({"x": 1})),
        ),
    ])


def test_log_entry_cannot_use_another_incidents_service(admin_conn):
    other_service = service_id(admin_conn, INCIDENT_B, "payment-router")
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        (
            "INSERT INTO evidence.evidence_index (evidence_id, incident_id, kind) "
            "VALUES ('L-a-9', %s, 'log')",
            (INCIDENT_A,),
        ),
        (
            "INSERT INTO evidence.log_entries "
            "(evidence_id, incident_id, service_id, ts, level, message) "
            "VALUES ('L-a-9', %s, %s, now(), 'INFO', 'x')",
            (INCIDENT_A, other_service),
        ),
    ])


def test_log_entry_must_exist_in_the_evidence_index(admin_conn):
    service = service_id(admin_conn, INCIDENT_A, "payment-router")
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        (
            "INSERT INTO evidence.log_entries "
            "(evidence_id, incident_id, service_id, ts, level, message) "
            "VALUES ('L-a-not-indexed', %s, %s, now(), 'INFO', 'x')",
            (INCIDENT_A, service),
        ),
    ])


def test_log_entry_cannot_reuse_the_id_of_a_change(admin_conn):
    """C-a-1 is indexed as a change; a log row claiming that id has the wrong kind."""
    service = service_id(admin_conn, INCIDENT_A, "payment-router")
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        (
            "INSERT INTO evidence.log_entries "
            "(evidence_id, incident_id, service_id, ts, level, message) "
            "VALUES ('C-a-1', %s, %s, now(), 'INFO', 'x')",
            (INCIDENT_A, service),
        ),
    ])


def index_dependency_sql(evidence_id, incident_id):
    return (
        "INSERT INTO evidence.evidence_index (evidence_id, incident_id, kind) "
        "VALUES (%s, %s, 'dependency')",
        (evidence_id, incident_id),
    )


DEPENDENCY_INSERT = (
    "INSERT INTO evidence.service_dependencies "
    "(evidence_id, incident_id, caller_id, callee_id) VALUES (%s, %s, %s, %s)"
)


def test_dependency_cannot_span_two_incidents(admin_conn):
    caller = service_id(admin_conn, INCIDENT_A, "bank-adapter")
    callee = service_id(admin_conn, INCIDENT_B, "bank-adapter")
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        index_dependency_sql("D-a-9", INCIDENT_A),
        (DEPENDENCY_INSERT, ("D-a-9", INCIDENT_A, caller, callee)),
    ])


def test_dependency_must_exist_in_the_evidence_index(admin_conn):
    caller = service_id(admin_conn, INCIDENT_A, "bank-adapter")
    callee = service_id(admin_conn, INCIDENT_A, "postgres")
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        (DEPENDENCY_INSERT, ("D-a-not-indexed", INCIDENT_A, caller, callee)),
    ])


def test_dependency_cannot_reuse_the_id_of_a_log(admin_conn):
    """L-a-1 is indexed as a log; a dependency row claiming that id has the wrong kind."""
    caller = service_id(admin_conn, INCIDENT_A, "bank-adapter")
    callee = service_id(admin_conn, INCIDENT_A, "postgres")
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        (DEPENDENCY_INSERT, ("L-a-1", INCIDENT_A, caller, callee)),
    ])


def test_dependency_cannot_use_another_incidents_evidence_id(admin_conn):
    caller = service_id(admin_conn, INCIDENT_A, "bank-adapter")
    callee = service_id(admin_conn, INCIDENT_A, "postgres")
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        index_dependency_sql("D-b-9", INCIDENT_B),
        (DEPENDENCY_INSERT, ("D-b-9", INCIDENT_A, caller, callee)),
    ])


def test_a_valid_dependency_edge_is_accepted(admin_conn):
    # The positive case, so the failures above cannot all come from the table being unusable.
    caller = service_id(admin_conn, INCIDENT_A, "bank-adapter")
    callee = service_id(admin_conn, INCIDENT_A, "postgres")
    with admin_conn.transaction(force_rollback=True):
        admin_conn.execute(*index_dependency_sql("D-a-9", INCIDENT_A))
        admin_conn.execute(DEPENDENCY_INSERT, ("D-a-9", INCIDENT_A, caller, callee))


def test_seeded_dependency_edges_carry_dependency_evidence_ids(admin_conn):
    rows = admin_conn.execute(
        "SELECT d.evidence_id, i.kind FROM evidence.service_dependencies d "
        "JOIN evidence.evidence_index i USING (incident_id, evidence_id)"
    ).fetchall()
    assert len(rows) > 0
    for evidence_id, kind in rows:
        assert evidence_id.startswith("D-")
        assert kind == "dependency"


def test_truth_can_point_at_a_dependency_and_withhold_one(admin_conn):
    with admin_conn.transaction(force_rollback=True):
        admin_conn.execute(
            "INSERT INTO truth.decoys (incident_id, evidence_id, decoy_type) "
            "VALUES (%s, 'D-a-1', 'unrelated_spike')",
            (INCIDENT_A,),
        )
        admin_conn.execute(
            "INSERT INTO truth.withheld_evidence (incident_id, kind, description) "
            "VALUES (%s, 'dependency', 'the edge that would show the pool size')",
            (INCIDENT_B,),
        )


def test_truth_cannot_reference_evidence_of_another_incident(admin_conn):
    for table, extra_column, extra_value in [
        ("truth.decisive_evidence", "availability", "retrievable"),
        ("truth.decoys", "decoy_type", "unrelated_spike"),
    ]:
        # 'C-b-1' belongs to incident B, so it is not evidence of incident A.
        expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
            (
                "INSERT INTO %s (incident_id, evidence_id, %s) VALUES (%%s, 'C-b-1', %%s)"
                % (table, extra_column),
                (INCIDENT_A, extra_value),
            ),
        ])


def test_truth_cannot_reference_evidence_that_does_not_exist(admin_conn):
    for table, extra_column, extra_value in [
        ("truth.decisive_evidence", "availability", "retrievable"),
        ("truth.decoys", "decoy_type", "unrelated_spike"),
    ]:
        expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
            (
                "INSERT INTO %s (incident_id, evidence_id, %s) VALUES (%%s, 'L-does-not-exist', %%s)"
                % (table, extra_column),
                (INCIDENT_A, extra_value),
            ),
        ])


# ------------------------------------------------------------ CHECK constraints


def test_incident_id_must_be_opaque_hash_format(admin_conn):
    for bad_id in ["pool_exhaustion_03", "inc_XYZ", "inc_aaaaaaa", "inc_aaaaaaaaa", ""]:
        expect_failure(admin_conn, psycopg.errors.CheckViolation, [new_incident_sql(bad_id)])


def test_incident_window_must_be_ordered(admin_conn):
    expect_failure(admin_conn, psycopg.errors.CheckViolation, [
        (
            "INSERT INTO evidence.incidents "
            "(incident_id, domain, reported_at, window_start, window_end, summary, reporter) "
            "VALUES ('inc_dddddddd', 'ott', now(), now(), now() - interval '1 hour', 's', 'r')",
            None,
        ),
    ])


def test_service_cannot_depend_on_itself(admin_conn):
    service = service_id(admin_conn, INCIDENT_A, "postgres")
    expect_failure(admin_conn, psycopg.errors.CheckViolation, [
        index_dependency_sql("D-a-9", INCIDENT_A),
        (DEPENDENCY_INSERT, ("D-a-9", INCIDENT_A, service, service)),
    ])


def test_evidence_id_prefix_must_match_kind(admin_conn):
    mismatches = [
        ("L-q", "metric"), ("M-q", "log"), ("X-q", "log"), ("C-q", "past_incident"),
        ("D-q", "log"), ("L-q", "dependency"),
    ]
    for evidence_id, kind in mismatches:
        expect_failure(admin_conn, psycopg.errors.CheckViolation, [
            (
                "INSERT INTO evidence.evidence_index (evidence_id, incident_id, kind) VALUES (%s, %s, %s)",
                (evidence_id, INCIDENT_A, kind),
            ),
        ])


def test_past_incident_symptoms_must_be_a_json_array(admin_conn):
    expect_failure(admin_conn, psycopg.errors.CheckViolation, [
        (
            "INSERT INTO evidence.evidence_index (evidence_id, incident_id, kind) "
            "VALUES ('P-a-9', %s, 'past_incident')",
            (INCIDENT_A,),
        ),
        (
            "INSERT INTO evidence.past_incidents "
            "(evidence_id, incident_id, occurred_at, title, root_cause_category, symptoms) "
            "VALUES ('P-a-9', %s, now(), 't', 'bad_deploy', %s)",
            (INCIDENT_A, Jsonb({"not": "an array"})),
        ),
    ])


def test_root_cause_category_is_restricted_to_the_ten_known_values(admin_conn):
    expect_failure(admin_conn, psycopg.errors.CheckViolation, [
        new_incident_sql("inc_dddddddd"),
        (
            "INSERT INTO truth.scenarios "
            "(incident_id, plan_id, archetype, seed, generator_version, expected_outcome, "
            " solvable_after_noise, root_component, root_category, mechanism) "
            "VALUES ('inc_dddddddd', 'T-1', 'a', 1, 't', 'identifiable', true, 'c', 'nonsense', 'm')",
            None,
        ),
    ])


def test_metric_series_are_unique_per_service_metric_and_source(admin_conn):
    """UNIQUE NULLS NOT DISTINCT: two fleet-wide (instance NULL) series collide."""
    service = service_id(admin_conn, INCIDENT_A, "payment-router")
    expect_failure(admin_conn, psycopg.errors.UniqueViolation, [
        (
            "INSERT INTO evidence.evidence_index (evidence_id, incident_id, kind) "
            "VALUES ('M-a-9', %s, 'metric')",
            (INCIDENT_A,),
        ),
        (
            "INSERT INTO evidence.metric_series "
            "(evidence_id, incident_id, service_id, instance, metric_name, unit, source, interval_s) "
            "VALUES ('M-a-9', %s, %s, NULL, 'cpu_pct', 'percent', 'prometheus', 60)",
            (INCIDENT_A, service),
        ),
    ])


# --------------------------------------------------- truth rules on one scenario row


SCENARIO_INSERT = (
    "INSERT INTO truth.scenarios "
    "(incident_id, plan_id, archetype, seed, generator_version, expected_outcome, "
    " solvable_after_noise, unidentifiable_reason, root_component, root_category, mechanism) "
    "VALUES ('inc_dddddddd', 'T-1', 'a', 1, 't', %s, %s, %s, %s, 'bad_deploy', 'm')"
)


@pytest.mark.parametrize("outcome,solvable,reason,component,error_class", [
    ("identifiable", False, None, "c", psycopg.errors.CheckViolation),            # outcome vs solvable
    ("inconclusive", True, "why", "c", psycopg.errors.CheckViolation),             # outcome vs solvable
    ("inconclusive", False, None, "c", psycopg.errors.CheckViolation),             # needs a reason
    ("identifiable", True, "unexpected reason", "c", psycopg.errors.CheckViolation),
    ("inconclusive", False, "why", None, psycopg.errors.NotNullViolation),         # real cause required
    ("maybe", True, None, "c", psycopg.errors.CheckViolation),                     # unknown outcome
])
def test_scenario_rules(admin_conn, outcome, solvable, reason, component, error_class):
    expect_failure(admin_conn, error_class, [
        new_incident_sql("inc_dddddddd"),
        (SCENARIO_INSERT, (outcome, solvable, reason, component)),
    ])


def test_eval_score_rules(admin_conn):
    run_sql = (
        "INSERT INTO eval.runs (run_id, incident_id, system, model, status) "
        "VALUES ('00000000-0000-0000-0000-000000000001', %s, 'single_agent', 'haiku', 'completed')",
        (INCIDENT_A,),
    )
    score_sql = (
        "INSERT INTO eval.scores (run_id, root_cause_correct, outcome_correct, grounding_cited, "
        "grounding_existing, decoy_hit, confidence, tool_call_count, llm_call_count, "
        "input_tokens, output_tokens, latency_ms, rounds) "
        "VALUES ('00000000-0000-0000-0000-000000000001', true, true, %s, %s, false, %s, 1, 1, 1, 1, 1, 1)"
    )

    # more existing citations than citations
    expect_failure(admin_conn, psycopg.errors.CheckViolation, [run_sql, (score_sql, (2, 3, 0.5))])
    # confidence outside [0, 1]
    expect_failure(admin_conn, psycopg.errors.CheckViolation, [run_sql, (score_sql, (3, 3, 1.5))])


# ------------------------------------------------------------------- cascades


def test_deleting_an_incident_removes_all_of_its_evidence_and_truth(admin_conn):
    with admin_conn.transaction(force_rollback=True):
        db_seed.seed_incident(admin_conn.cursor(), "inc_cccccccc", "c", "identifiable", with_run=False)

        for table in list_tables(admin_conn, "evidence"):
            count = admin_conn.execute(
                "SELECT count(*) FROM evidence.%s WHERE incident_id = 'inc_cccccccc'" % table
            ).fetchone()[0]
            assert count > 0, table + " was not seeded for the cascade test"

        admin_conn.execute("DELETE FROM evidence.incidents WHERE incident_id = 'inc_cccccccc'")

        for schema in ["evidence", "truth"]:
            for table in list_tables(admin_conn, schema):
                count = admin_conn.execute(
                    "SELECT count(*) FROM %s.%s WHERE incident_id = 'inc_cccccccc'" % (schema, table)
                ).fetchone()
                assert count[0] == 0, "%s.%s kept rows after the cascade" % (schema, table)


def test_incident_with_results_cannot_be_deleted(admin_conn):
    """eval.runs deliberately has no ON DELETE CASCADE, so results are never wiped silently."""
    expect_failure(admin_conn, psycopg.errors.ForeignKeyViolation, [
        ("DELETE FROM evidence.incidents WHERE incident_id = %s", (INCIDENT_A,)),
    ])
