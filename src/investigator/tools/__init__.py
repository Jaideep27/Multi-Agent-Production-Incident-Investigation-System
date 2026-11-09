"""The evidence tools behind the MCP server.

backend.py           EvidenceBackend interface (what a data source must answer)
postgres_backend.py  PostgresBackend: agent_ro pool, incident bound per transaction
failures.py          deterministic failure injection
service.py           ToolService: validation, attempts, latency, injection, result envelope
"""
