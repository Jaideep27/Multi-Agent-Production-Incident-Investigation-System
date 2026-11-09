"""The MCP server on Postgres: parallel calls through the protocol, and the real stdio entry point."""

import asyncio
import os
import sys
import time

import pytest
import pytest_asyncio
from mcp import Client, StdioServerParameters

from investigator.mcp_server import build_server, envelope_from_result
from investigator.tools.failures import FailureInjector
from investigator.tools.postgres_backend import PostgresBackend, open_agent_pool
from investigator.tools.service import ToolService

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

LATENCY_MS = 150


@pytest_asyncio.fixture
async def pool(pg_database):
    agent_pool = await open_agent_pool(min_size=1, max_size=4)
    yield agent_pool
    await agent_pool.close()


async def test_twenty_parallel_calls_overlap_and_match_direct_calls(pg1, pool):
    incident_id = pg1.evidence.incident.incident_id
    backend = PostgresBackend(pool, incident_id)
    no_failures = FailureInjector(seed=incident_id, rules=[])
    slow = ToolService(backend=backend, injector=no_failures, latency_ms=LATENCY_MS)
    direct = ToolService(backend=backend, injector=no_failures)

    requests = []
    for log in pg1.evidence.logs[:20]:
        requests.append(["query_logs", {"service": log.service, "start": log.ts.isoformat(),
                                        "limit": 5}])

    async with Client(build_server(slow)) as client:
        started = time.monotonic()
        calls = []
        for tool, args in requests:
            calls.append(client.call_tool(tool, args))
        results = await asyncio.gather(*calls)
        elapsed = time.monotonic() - started

    serial_seconds = len(requests) * LATENCY_MS / 1000.0
    assert elapsed < serial_seconds / 2

    for number in range(len(requests)):
        tool, args = requests[number]
        expected = await direct.call(tool, args)
        received = envelope_from_result(results[number])
        assert received.status == "ok"
        assert received.items == expected.items


def stdio_parameters(incident_id):
    env = dict(os.environ)    # the pg_database fixture put the IV_PG_* settings and passwords here
    if incident_id is None:
        env.pop("INCIDENT_ID", None)
    else:
        env["INCIDENT_ID"] = incident_id
    return StdioServerParameters(command=sys.executable, args=["-m", "investigator.mcp_server"],
                                 env=env)


async def test_stdio_server_answers_a_tool_call(pg1):
    incident_id = pg1.evidence.incident.incident_id
    async with Client(stdio_parameters(incident_id)) as client:
        listed = await client.list_tools()
        assert len(listed.tools) == 5
        first = await client.call_tool("get_service_dependencies", {})
        # PG-1's rule: each distinct log query times out on its first attempt.
        log_first = await client.call_tool("query_logs", {"limit": 3})
        log_second = await client.call_tool("query_logs", {"limit": 3})

    assert envelope_from_result(first).status == "ok"
    assert envelope_from_result(log_first).status == "timeout"
    assert envelope_from_result(log_second).status == "ok"


def run_server_expecting_failure(incident_id):
    import subprocess
    parameters = stdio_parameters(incident_id)
    return subprocess.run([parameters.command] + parameters.args, env=parameters.env,
                          input="", capture_output=True, text=True, timeout=60)


async def test_stdio_server_refuses_to_start_without_an_incident(pg_database):
    result = run_server_expecting_failure(None)
    assert result.returncode != 0
    assert "INCIDENT_ID" in result.stderr


async def test_stdio_server_refuses_a_malformed_incident_id(pg_database):
    result = run_server_expecting_failure("inc_x' OR 1=1 --")
    assert result.returncode != 0
    assert "INCIDENT_ID" in result.stderr


async def test_stdio_server_refuses_an_unknown_incident(pg_database):
    result = run_server_expecting_failure("inc_0badf00d")
    assert result.returncode != 0
    assert "inc_0badf00d" in result.stderr
