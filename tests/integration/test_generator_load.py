"""Loading a generated incident into Postgres as generator_rw.

The pg1 fixture (tests/conftest.py) loads PG-1 once per module and deletes it afterwards,
so the other database tests still see only the two seed incidents.
"""

import pytest

from investigator import db
from investigator.generator import load as load_module
from investigator.generator.build import build_incident
from investigator.generator.dataset import DecoyRow, dataset_checksum
from investigator.generator.load import GeneratorError, load_incident
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.generator.read import read_dataset
from investigator.generator.validate import forbidden_tokens
from tests.conftest import list_tables

pytestmark = pytest.mark.db


def checksum_in_database(connection, incident_id):
    return dataset_checksum(read_dataset(connection, incident_id))


def test_loaded_incident_has_no_invariant_violations(pg1, admin_conn):
    rows = admin_conn.execute(
        "SELECT violation FROM truth.invariant_violations WHERE incident_id = %s",
        (pg1.evidence.incident.incident_id,),
    ).fetchall()
    assert rows == []


def test_database_round_trip_matches_the_build(pg1, connect_as):
    scorer = connect_as("scorer_ro", autocommit=True)
    loaded = read_dataset(scorer, pg1.evidence.incident.incident_id)
    assert loaded == pg1
    assert dataset_checksum(loaded) == dataset_checksum(pg1)


def test_every_evidence_row_is_in_the_index(pg1, admin_conn):
    incident_id = pg1.evidence.incident.incident_id
    indexed = admin_conn.execute(
        "SELECT count(*) FROM evidence.evidence_index WHERE incident_id = %s", (incident_id,)
    ).fetchone()[0]
    expected = (
        len(pg1.evidence.dependencies)
        + len(pg1.evidence.logs)
        + len(pg1.evidence.metrics)
        + len(pg1.evidence.changes)
        + len(pg1.evidence.past_incidents)
    )
    assert indexed == expected


def test_loading_again_replaces_the_incident(pg1, connect_as, admin_conn):
    incident_id = pg1.evidence.incident.incident_id
    generator = connect_as("generator_rw")
    before = checksum_in_database(generator, incident_id)

    load_incident(generator, build_incident(find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1")))

    count = admin_conn.execute(
        "SELECT count(*) FROM truth.scenarios WHERE plan_id = 'PG-1'"
    ).fetchone()[0]
    assert count == 1
    assert checksum_in_database(generator, incident_id) == before


def test_invalid_dataset_is_refused_and_the_old_one_kept(pg1, connect_as):
    incident_id = pg1.evidence.incident.incident_id
    generator = connect_as("generator_rw")
    before = checksum_in_database(generator, incident_id)

    broken = pg1.model_copy(deep=True)
    broken.truth.decoys.append(
        DecoyRow(evidence_id=broken.truth.decisive[0].evidence_id, decoy_type="unrelated_spike")
    )
    with pytest.raises(GeneratorError, match="evidence_is_both_decisive_and_decoy"):
        load_incident(generator, broken)

    assert checksum_in_database(generator, incident_id) == before


def test_database_invariant_check_runs_even_if_python_checks_miss(pg1, connect_as, monkeypatch):
    # Defence in depth: pretend the Python validator has a bug and lets a bad dataset through.
    incident_id = pg1.evidence.incident.incident_id
    generator = connect_as("generator_rw")
    before = checksum_in_database(generator, incident_id)

    def no_problems(dataset):
        return []

    monkeypatch.setattr(load_module, "find_problems", no_problems)
    broken = pg1.model_copy(deep=True)
    broken.truth.decoys.append(
        DecoyRow(evidence_id=broken.truth.decisive[0].evidence_id, decoy_type="unrelated_spike")
    )
    with pytest.raises(GeneratorError, match="invariant_violations"):
        load_incident(generator, broken)

    assert checksum_in_database(generator, incident_id) == before


def test_agent_sees_the_generated_incident_only_when_bound(pg1, connect_as):
    incident_id = pg1.evidence.incident.incident_id
    agent = connect_as("agent_ro")
    with db.incident_transaction(agent, incident_id) as cursor:
        cursor.execute("SELECT count(*) FROM evidence.log_entries")
        assert cursor.fetchone()[0] == len(pg1.evidence.logs)
    with agent.transaction():
        count = agent.execute("SELECT count(*) FROM evidence.log_entries").fetchone()[0]
        assert count == 0


def test_agent_visible_rows_contain_no_answer_key_text(pg1, connect_as, admin_conn):
    incident_id = pg1.evidence.incident.incident_id
    tokens = forbidden_tokens(pg1)
    agent = connect_as("agent_ro")
    rows_seen = 0
    leaks = []
    with db.incident_transaction(agent, incident_id) as cursor:
        for table in list_tables(admin_conn, "evidence"):
            cursor.execute("SELECT t::text FROM evidence.%s AS t" % table)
            for row in cursor.fetchall():
                rows_seen += 1
                text = row[0].lower()
                for token in tokens:
                    if token in text:
                        leaks.append("%s: %s" % (table, token))
    assert rows_seen > 1000
    assert leaks == []
