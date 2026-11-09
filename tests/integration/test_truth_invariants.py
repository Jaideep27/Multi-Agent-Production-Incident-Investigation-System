"""Truth-schema invariants (correction #6).

An inconclusive incident must keep its REAL ground truth while its visible evidence stays
insufficient. truth.invariant_violations expresses the cross-table rules; it must be empty
for good data and must catch each way the data can go wrong.
"""

import pytest

from tests.db_seed import INCIDENT_A, INCIDENT_B

pytestmark = pytest.mark.db


def violations_for(connection, incident_id):
    rows = connection.execute(
        "SELECT violation FROM truth.invariant_violations WHERE incident_id = %s", (incident_id,)
    ).fetchall()
    found = set()
    for row in rows:
        found.add(row[0])
    return found


def test_seed_data_has_no_violations(admin_conn):
    rows = admin_conn.execute("SELECT incident_id, violation FROM truth.invariant_violations").fetchall()
    assert rows == []


def test_scorer_can_read_the_violations_view(connect_as):
    connection = connect_as("scorer_ro", autocommit=True)
    rows = connection.execute("SELECT * FROM truth.invariant_violations").fetchall()
    assert rows == []


def test_inconclusive_incident_keeps_its_real_ground_truth(admin_conn):
    row = admin_conn.execute(
        """
        SELECT expected_outcome, solvable_after_noise, unidentifiable_reason,
               root_component, root_category, mechanism
          FROM truth.scenarios WHERE incident_id = %s
        """,
        (INCIDENT_B,),
    ).fetchone()

    outcome, solvable, reason, component, category, mechanism = row
    assert outcome == "inconclusive"
    assert solvable is False
    assert reason
    # the real cause is still recorded
    assert component == "payment-router"
    assert category == "config_change"
    assert mechanism


def test_inconclusive_incident_has_no_retrievable_decisive_evidence(admin_conn):
    rows = admin_conn.execute(
        "SELECT availability FROM truth.decisive_evidence WHERE incident_id = %s", (INCIDENT_B,)
    ).fetchall()
    assert rows
    for row in rows:
        assert row[0] == "unreachable"

    withheld = admin_conn.execute(
        "SELECT count(*) FROM truth.withheld_evidence WHERE incident_id = %s", (INCIDENT_B,)
    ).fetchone()[0]
    alternatives = admin_conn.execute(
        "SELECT count(*) FROM truth.alternative_explanations WHERE incident_id = %s", (INCIDENT_B,)
    ).fetchone()[0]
    assert withheld >= 1
    assert alternatives >= 1


def test_identifiable_incident_has_retrievable_decisive_evidence_only(admin_conn):
    rows = admin_conn.execute(
        "SELECT availability FROM truth.decisive_evidence WHERE incident_id = %s", (INCIDENT_A,)
    ).fetchall()
    assert rows
    for row in rows:
        assert row[0] == "retrievable"


# Each case breaks the data in one way and names the violation the view must report.
BREAKAGES = [
    (
        "identifiable_without_retrievable_decisive_evidence",
        INCIDENT_A,
        ["DELETE FROM truth.decisive_evidence WHERE incident_id = 'inc_aaaaaaaa'"],
    ),
    (
        "identifiable_with_unreachable_decisive_evidence",
        INCIDENT_A,
        ["UPDATE truth.decisive_evidence SET availability = 'unreachable' "
         "WHERE incident_id = 'inc_aaaaaaaa' AND evidence_id = 'C-a-1'"],
    ),
    (
        "identifiable_with_withheld_evidence",
        INCIDENT_A,
        ["INSERT INTO truth.withheld_evidence (incident_id, kind, description) "
         "VALUES ('inc_aaaaaaaa', 'log', 'should not exist for a solvable incident')"],
    ),
    (
        "inconclusive_with_retrievable_decisive_evidence",
        INCIDENT_B,
        ["UPDATE truth.decisive_evidence SET availability = 'retrievable' "
         "WHERE incident_id = 'inc_bbbbbbbb'"],
    ),
    (
        "inconclusive_without_unreachable_or_withheld_evidence",
        INCIDENT_B,
        ["DELETE FROM truth.decisive_evidence WHERE incident_id = 'inc_bbbbbbbb'",
         "DELETE FROM truth.withheld_evidence WHERE incident_id = 'inc_bbbbbbbb'"],
    ),
    (
        "inconclusive_without_alternative_explanation",
        INCIDENT_B,
        ["DELETE FROM truth.alternative_explanations WHERE incident_id = 'inc_bbbbbbbb'"],
    ),
    (
        "evidence_is_both_decisive_and_decoy",
        INCIDENT_A,
        ["INSERT INTO truth.decoys (incident_id, evidence_id, decoy_type) "
         "VALUES ('inc_aaaaaaaa', 'C-a-1', 'unrelated_spike')"],
    ),
]


@pytest.mark.parametrize("expected_violation,incident_id,statements", BREAKAGES)
def test_view_catches_each_kind_of_violation(admin_conn, expected_violation, incident_id, statements):
    with admin_conn.transaction(force_rollback=True):
        for statement in statements:
            admin_conn.execute(statement)

        found = violations_for(admin_conn, incident_id)
        assert expected_violation in found, "view reported %s" % sorted(found)

    # the seed is back to clean after the rollback
    assert violations_for(admin_conn, incident_id) == set()
