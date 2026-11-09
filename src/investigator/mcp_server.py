"""MCP server exposing the five evidence tools for ONE incident.

Run:  INCIDENT_ID=inc_xxxxxxxx python -m investigator.mcp_server   (stdio transport)

The incident is fixed at start-up and is never a tool argument. The server connects as
agent_ro only and offers no raw SQL. It refuses to start without a well-formed INCIDENT_ID
that exists in the database.

Built on the low-level mcp Server (mcp 2.x) rather than the decorator API, so each tool's
input schema is exactly its Pydantic args model (unknown fields forbidden) and the raw
arguments reach ToolService, which validates them itself.
"""

import asyncio
import json
import os
import re
import sys

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from investigator.models.tools import ARGS_MODELS, ToolResult
from investigator.tools.failures import FailureInjector
from investigator.tools.service import ToolService, UnknownToolError

SERVER_NAME = "incident-evidence"

# What the model reads when it chooses a tool. Keep these about the tools, never about any
# particular incident.
TOOL_DESCRIPTIONS = {
    "query_logs": (
        "Search log lines of the incident's services, oldest first. Filter by service, by "
        "levels (e.g. [\"ERROR\", \"WARN\"]), by a case-insensitive substring ('contains') and "
        "by a time range [start, end). Results are paged: when 'truncated' is true, call again "
        "with the same filters and cursor=next_cursor."
    ),
    "query_metrics": (
        "Fetch metric time series (one item per series, with min/max/mean/first/last stats and "
        "the points). Filter by service, metric_name and instance; instance null in a result "
        "means a fleet-wide aggregate. A time range [start, end) narrows the points; 'limit' "
        "caps the number of series."
    ),
    "get_change_history": (
        "List deploys, config changes, scaling and other changes, oldest first. Summaries "
        "only by default: set include_diff=true (optionally with change_ids) to see what "
        "actually changed."
    ),
    "get_service_dependencies": (
        "Show call edges between services with their client configuration (timeouts, retries, "
        "pool sizes). direction='downstream' lists what the service calls, 'upstream' lists "
        "who calls the service, 'both' lists both; omit service for the whole graph."
    ),
    "search_past_incidents": (
        "Search earlier incidents by words in their title, symptoms and resolution; best "
        "matches first. A similar past incident is a lead to check, not proof."
    ),
}

TOOL_ORDER = [
    "query_logs",
    "query_metrics",
    "get_change_history",
    "get_service_dependencies",
    "search_past_incidents",
]

INCIDENT_ID_PATTERN = r"^inc_[0-9a-f]{8}$"


def tool_definitions():
    tools = []
    for name in TOOL_ORDER:
        schema = ARGS_MODELS[name].model_json_schema()
        tools.append(types.Tool(name=name, description=TOOL_DESCRIPTIONS[name], input_schema=schema))
    return tools


def result_from_envelope(envelope):
    payload = envelope.model_dump(mode="json")
    failed = envelope.status == "timeout" or envelope.status == "error"
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload))],
        structured_content=payload,
        is_error=failed,
    )


def envelope_from_result(result):
    """The ToolResult inside an MCP CallToolResult (what the gateway reads)."""
    return ToolResult.model_validate(result.structured_content)


def build_server(service):
    """An MCP server whose tool calls all go through one ToolService."""

    async def on_list_tools(context, params):
        return types.ListToolsResult(tools=tool_definitions())

    async def on_call_tool(context, params):
        try:
            envelope = await service.call(params.name, params.arguments)
        except UnknownToolError as error:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(error))],
                is_error=True,
            )
        return result_from_envelope(envelope)

    return Server(SERVER_NAME, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def serve(incident_id, pool_max_size, latency_ms):
    # Imported here so that building a server for tests does not need psycopg_pool.
    from investigator.tools.postgres_backend import PostgresBackend, open_agent_pool

    pool = await open_agent_pool(min_size=1, max_size=pool_max_size)
    try:
        backend = PostgresBackend(pool, incident_id)
        await backend.check_incident_exists()
        injector = FailureInjector(seed=incident_id, rules=await backend.failure_rules())
        service = ToolService(backend=backend, injector=injector, latency_ms=latency_ms)
        server = build_server(service)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await pool.close()


def main():
    incident_id = os.environ.get("INCIDENT_ID", "")
    if not re.fullmatch(INCIDENT_ID_PATTERN, incident_id):
        print("INCIDENT_ID must be set to an incident ID like inc_1a2b3c4d", file=sys.stderr)
        return 2
    pool_max_size = int(os.environ.get("IV_MCP_POOL_MAX", "4"))
    latency_ms = int(os.environ.get("IV_MCP_LATENCY_MS", "0"))
    # Async psycopg cannot run on Windows' default ProactorEventLoop. This process starts no
    # subprocesses, so the selector loop is safe here.
    loop_factory = None
    if os.name == "nt":
        loop_factory = asyncio.SelectorEventLoop
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(serve(incident_id, pool_max_size, latency_ms))
    except LookupError as error:
        print("cannot start: %s" % error, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
