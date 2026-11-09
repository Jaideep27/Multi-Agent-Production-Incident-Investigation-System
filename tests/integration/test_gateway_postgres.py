"""The gateway on the real chain: Gateway -> MCP client -> MCP server -> ToolService -> Postgres,
with every attempt logged to eval.tool_calls as harness_rw."""

import time
import uuid

import psycopg
import pytest
import pytest_asyncio
from mcp import Client

from investigator import db
from investigator.gateway.call_log import PostgresCallLog
from investigator.gateway.caller import McpToolCaller
from investigator.gateway.gateway import Gateway, GatewayLimits
from investigator.mcp_server import build_server
from investigator.models.tools import ToolRequest
from investigator.tools.failures import FailureInjector
from investigator.tools.postgres_backend import PostgresBackend, open_agent_pool
from investigator.tools.service import ToolService

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def pool(pg_database):
    agent_pool = await open_agent_pool(min_size=1, max_size=4)
    yield agent_pool
    await agent_pool.close()


@pytest.fixture(autouse=True)
def remove_test_runs(pg1, admin_conn):
    """Runs protect their incident from deletion (no cascade, by design), so the runs these
    tests create must go before the pg1 fixture can delete PG-1. harness_rw cannot delete
    runs, so the admin connection does it."""
    yield
    admin_conn.execute("DELETE FROM eval.runs WHERE incident_id = %s",
                       (pg1.evidence.incident.incident_id,))


@pytest_asyncio.fixture
async def harness(pg_database):
    connection = await psycopg.AsyncConnection.connect(db.conninfo_for("harness_rw"),
                                                       autocommit=True)
    yield connection
    await connection.close()


async def new_run(harness, incident_id):
    run_id = str(uuid.uuid4())
    await harness.execute(
        "INSERT INTO eval.runs (run_id, incident_id, system, model, status) "
        "VALUES (%s, %s, 'multi_agent', 'test', 'running')",
        (run_id, incident_id),
    )
    return run_id


async def logged_attempts(harness, run_id):
    cursor = await harness.execute(
        "SELECT tool, attempt, status, latency_ms, evidence_ids, args FROM eval.tool_calls "
        "WHERE run_id = %s ORDER BY call_id",
        (run_id,),
    )
    return await cursor.fetchall()


def server_for(pool, incident_id, rules, latency_ms=0):
    backend = PostgresBackend(pool, incident_id)
    service = ToolService(backend=backend, injector=FailureInjector(seed=incident_id, rules=rules),
                          latency_ms=latency_ms)
    return build_server(service)


async def test_transient_log_timeout_is_retried_and_both_attempts_logged(pg1, pool, harness):
    incident_id = pg1.evidence.incident.incident_id
    run_id = await new_run(harness, incident_id)
    pg1_rules = []
    for row in pg1.evidence.tool_behavior:
        pg1_rules.append([row.tool_name, row.rules])

    async with Client(server_for(pool, incident_id, pg1_rules)) as client:
        gateway = Gateway(caller=McpToolCaller(client), run_id=run_id,
                          limits=GatewayLimits(retry_delay_s=0.0), log=PostgresCallLog(harness))
        outcomes = await gateway.execute([
            ToolRequest(tool="query_logs", args={"levels": ["ERROR"], "limit": 5}),
        ])

    outcome = outcomes[0]
    assert outcome.attempts == 2
    assert outcome.result.status == "ok"
    rows = await logged_attempts(harness, run_id)
    assert len(rows) == 2
    assert [rows[0][1], rows[0][2]] == [1, "timeout"]
    assert [rows[1][1], rows[1][2]] == [2, "ok"]
    assert rows[0][4] == []
    assert rows[1][4] == outcome.result_evidence_ids()
    assert rows[1][5]["levels"] == ["ERROR"]


async def test_persistent_failure_is_logged_exactly_three_times(pg1, pool, harness):
    incident_id = pg1.evidence.incident.incident_id
    run_id = await new_run(harness, incident_id)
    rules = [["get_change_history", {"mode": "persistent", "when": {"include_diff": True}}]]

    async with Client(server_for(pool, incident_id, rules)) as client:
        gateway = Gateway(caller=McpToolCaller(client), run_id=run_id,
                          limits=GatewayLimits(retry_delay_s=0.0), log=PostgresCallLog(harness))
        outcomes = await gateway.execute([
            ToolRequest(tool="get_change_history", args={"include_diff": True}),
            ToolRequest(tool="get_change_history", args={}),
        ])

    assert outcomes[0].attempts == 3
    assert outcomes[0].result.status == "timeout"
    assert outcomes[1].attempts == 1
    assert outcomes[1].result.status == "ok"
    rows = await logged_attempts(harness, run_id)
    failed = []
    for row in rows:
        if row[2] == "timeout":
            failed.append(row[1])
    assert failed == [1, 2, 3]
    assert len(rows) == 4


async def test_twenty_calls_through_the_gateway_run_in_parallel(pg1, pool, harness):
    incident_id = pg1.evidence.incident.incident_id
    run_id = await new_run(harness, incident_id)
    latency_ms = 150
    requests = []
    for log in pg1.evidence.logs[:20]:
        requests.append(ToolRequest(tool="query_logs",
                                    args={"service": log.service, "start": log.ts.isoformat(),
                                          "limit": 5}))

    async with Client(server_for(pool, incident_id, [], latency_ms=latency_ms)) as client:
        gateway = Gateway(caller=McpToolCaller(client), run_id=run_id,
                          limits=GatewayLimits(max_concurrency=10), log=PostgresCallLog(harness))
        started = time.monotonic()
        outcomes = await gateway.execute(requests)
        elapsed = time.monotonic() - started

    assert elapsed < 20 * latency_ms / 1000.0 / 2
    for number in range(20):
        result = outcomes[number].result
        assert result.status == "ok"
        assert result.items[0].data["service"] == requests[number].args["service"]
    rows = await logged_attempts(harness, run_id)
    assert len(rows) == 20


async def test_unknown_tool_reply_without_an_envelope_is_an_error(pg1, pool):
    # McpToolCaller must not invent a result when the server sends no envelope.
    incident_id = pg1.evidence.incident.incident_id
    async with Client(server_for(pool, incident_id, [])) as client:
        caller = McpToolCaller(client)
        with pytest.raises(RuntimeError, match="unknown tool"):
            await caller.call("run_sql", {"sql": "select 1"})
