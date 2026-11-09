"""The MCP server over the real MCP protocol (in-process client), with a fake backend.

tests/integration/test_mcp_postgres.py repeats the parallel test against Postgres and runs
the server as a real stdio subprocess.
"""

import asyncio
import json
import time
import typing

import pytest
from mcp import Client

from investigator.mcp_server import build_server, envelope_from_result
from investigator.models.common import ToolName
from investigator.models.tools import ARGS_MODELS
from investigator.tools.failures import FailureInjector
from investigator.tools.service import ToolService
from tests.fakes import FakeBackend


def make_server(rules=None, latency_ms=0):
    backend = FakeBackend()
    injector = FailureInjector(seed="inc_0000000a", rules=rules or [])
    service = ToolService(backend=backend, injector=injector, latency_ms=latency_ms)
    return build_server(service), backend


@pytest.mark.asyncio
async def test_lists_exactly_the_five_tools_with_strict_schemas():
    server, backend = make_server()
    async with Client(server) as client:
        listed = await client.list_tools()
    names = []
    for tool in listed.tools:
        names.append(tool.name)
        schema = tool.input_schema
        assert schema["additionalProperties"] is False
        assert "incident_id" not in schema.get("properties", {})
        assert tool.description
    assert sorted(names) == sorted(typing.get_args(ToolName))
    assert sorted(names) == sorted(ARGS_MODELS.keys())


@pytest.mark.asyncio
async def test_call_returns_the_envelope_as_structured_content_and_text():
    server, backend = make_server()
    async with Client(server) as client:
        result = await client.call_tool("query_logs", {"service": "api"})
    envelope = envelope_from_result(result)
    assert envelope.status == "ok"
    assert len(envelope.items) == 6
    assert json.loads(result.content[0].text) == result.structured_content
    assert result.is_error is False


@pytest.mark.asyncio
async def test_injected_timeout_is_flagged_as_a_tool_error():
    server, backend = make_server(rules=[["query_logs", {"mode": "transient"}]])
    async with Client(server) as client:
        result = await client.call_tool("query_logs", {})
    assert result.is_error is True
    assert envelope_from_result(result).status == "timeout"


@pytest.mark.asyncio
async def test_unknown_tool_is_an_error_without_an_envelope():
    server, backend = make_server()
    async with Client(server) as client:
        result = await client.call_tool("run_sql", {"sql": "select 1"})
    assert result.is_error is True
    assert "unknown tool" in result.content[0].text
    assert backend.calls == []


@pytest.mark.asyncio
async def test_extra_incident_id_argument_is_refused():
    server, backend = make_server()
    async with Client(server) as client:
        result = await client.call_tool("query_logs", {"incident_id": "inc_0000000b"})
    assert envelope_from_result(result).status == "error"
    assert backend.calls == []


@pytest.mark.asyncio
async def test_twenty_parallel_calls_overlap_and_match_their_requests():
    server, backend = make_server(latency_ms=200)
    async with Client(server) as client:
        started = time.monotonic()
        calls = []
        for number in range(20):
            calls.append(client.call_tool("query_logs", {"contains": "marker-%d" % number}))
        results = await asyncio.gather(*calls)
        elapsed = time.monotonic() - started

    assert elapsed < 1.5          # serial would be 20 x 0.2 = 4.0 s
    for number in range(20):
        envelope = envelope_from_result(results[number])
        assert envelope.status == "ok"
        for item in envelope.items:
            assert item.data["request"]["contains"] == "marker-%d" % number
    received = []
    for tool, args in backend.calls:
        received.append(args["contains"])
    expected = []
    for number in range(20):
        expected.append("marker-%d" % number)
    assert sorted(received) == sorted(expected)
