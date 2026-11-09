"""Step 9 gate (part 2): solvability audit over all 25 incidents.

Each incident is loaded and probed through the real tool path (ToolService + PostgresBackend
as agent_ro, with the incident's own failure rules from evidence.tool_behavior). Every
decisive item marked retrievable must come back from at least one natural query within the
gateway's 3 attempts; every item marked unreachable must never come back. For a change the
decisive content is its diff, so change probes ask for diffs.
"""

from datetime import timedelta

import psycopg
import pytest
import pytest_asyncio

from investigator import db
from investigator.generator.build import build_incident
from investigator.generator.load import delete_plan, load_incident
from investigator.generator.plan import DEFAULT_PLAN_PATH, load_plan
from investigator.tools.failures import FailureInjector
from investigator.tools.postgres_backend import PostgresBackend, open_agent_pool
from investigator.tools.service import ToolService

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

ATTEMPTS = 3


@pytest.fixture(scope="module")
def all_incidents(pg_database):
    entries = load_plan(DEFAULT_PLAN_PATH).incidents
    connection = psycopg.connect(db.conninfo_for("generator_rw"))
    datasets = []
    for entry in entries:
        dataset = build_incident(entry)
        load_incident(connection, dataset)
        datasets.append(dataset)
    yield datasets
    for entry in entries:
        delete_plan(connection, entry.plan_id)
    connection.close()


@pytest_asyncio.fixture
async def pool(pg_database):
    agent_pool = await open_agent_pool(min_size=1, max_size=4)
    yield agent_pool
    await agent_pool.close()


def items_by_id(dataset):
    found = {}
    evidence = dataset.evidence
    for group in [evidence.dependencies, evidence.logs, evidence.metrics, evidence.changes,
                  evidence.past_incidents]:
        for item in group:
            found[item.evidence_id] = item
    return found


def probes_for(item):
    """[tool, args] queries an investigator would naturally use to reach this item."""
    evidence_id = item.evidence_id
    if evidence_id.startswith("L-"):
        return [
            ["query_logs", {"service": item.service, "start": item.ts.isoformat(),
                            "end": (item.ts + timedelta(seconds=1)).isoformat(), "limit": 200}],
            ["query_logs", {"service": item.service, "levels": [item.level],
                            "start": (item.ts - timedelta(minutes=1)).isoformat(),
                            "end": (item.ts + timedelta(minutes=1)).isoformat(), "limit": 200}],
            ["query_logs", {"contains": item.message[:40], "limit": 200}],
        ]
    if evidence_id.startswith("M-"):
        return [
            ["query_metrics", {"service": item.service, "metric_name": item.metric_name,
                               "limit": 100}],
            ["query_metrics", {"service": item.service, "limit": 100}],
            ["query_metrics", {"metric_name": item.metric_name, "limit": 100}],
        ]
    if evidence_id.startswith("C-"):
        return [
            ["get_change_history", {"change_ids": [evidence_id], "include_diff": True}],
            ["get_change_history", {"service": item.service, "include_diff": True}],
            ["get_change_history", {"include_diff": True, "limit": 200}],
        ]
    if evidence_id.startswith("D-"):
        return [
            ["get_service_dependencies", {"service": item.caller, "direction": "downstream"}],
            ["get_service_dependencies", {}],
        ]
    return [["search_past_incidents", {"query": item.title}]]


async def reachable(service, item):
    for tool, args in probes_for(item):
        for attempt in range(ATTEMPTS):
            result = await service.call(tool, args)
            if result.status in ["timeout", "error"]:
                continue
            for returned in result.items:
                if returned.evidence_id == item.evidence_id:
                    return True
            break
    return False


async def test_every_decisive_item_is_reachable_exactly_when_the_answer_key_says_so(
        all_incidents, pool):
    failures = []
    checked = 0
    for dataset in all_incidents:
        incident_id = dataset.evidence.incident.incident_id
        backend = PostgresBackend(pool, incident_id)
        rules = await backend.failure_rules()
        service = ToolService(backend=backend, injector=FailureInjector(seed=incident_id,
                                                                        rules=rules))
        lookup = items_by_id(dataset)
        plan_id = dataset.truth.scenario.plan_id
        for row in dataset.truth.decisive:
            checked = checked + 1
            got = await reachable(service, lookup[row.evidence_id])
            expected = row.availability == "retrievable"
            if got != expected:
                failures.append("%s %s is %s but reachable=%s"
                                % (plan_id, row.evidence_id, row.availability, got))
    assert checked >= 50
    assert failures == []


async def test_no_incident_breaks_the_truth_invariants(all_incidents, admin_conn):
    rows = admin_conn.execute("SELECT incident_id, violation FROM truth.invariant_violations").fetchall()
    assert rows == []


async def test_withheld_evidence_is_really_absent(all_incidents, admin_conn):
    for dataset in all_incidents:
        for row in dataset.truth.withheld:
            if row.kind == "log" and row.would_have_shown:
                message = row.would_have_shown["message"]
                found = admin_conn.execute(
                    "SELECT count(*) FROM evidence.log_entries WHERE incident_id = %s "
                    "AND message = %s", (dataset.evidence.incident.incident_id, message)).fetchone()[0]
                assert found == 0, (dataset.truth.scenario.plan_id, message)
