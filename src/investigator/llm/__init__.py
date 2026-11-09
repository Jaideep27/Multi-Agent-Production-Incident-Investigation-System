"""LLM access for the agents.

backend.py     Completion model and the LlmBackend interface
claude_cli.py  ClaudeCliBackend: `claude -p` as a subprocess (tools off, safe mode, neutral cwd)
stub.py        StubBackend: scripted answers for tests and stub runs (no quota)
cache.py       ResponseCache: record/replay of completions, so reruns are reproducible
client.py      LlmClient.structured(): JSON extraction, validation, repair retries, limits
call_log.py    PostgresLlmCallLog: one eval.llm_calls row per attempt (harness_rw)
"""
