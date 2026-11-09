"""The ToolGateway: the only path from an agent's tool_requests to the MCP server.

gateway.py   Gateway, GatewayLimits, GatewayOutcome (parallelism, retries, de-dupe, budgets)
caller.py    McpToolCaller: one tool call over an MCP client session
call_log.py  PostgresCallLog: one eval.tool_calls row per physical attempt (harness_rw)

The single-agent baseline goes through the same Gateway, so both systems get identical
retries, budgets and logging.
"""
