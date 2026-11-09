"""One real `claude -p` call through the whole wrapper. Spends a tiny amount of quota.

Skipped unless IV_LIVE_LLM=1 (needs an authenticated Claude CLI on PATH):
    IV_LIVE_LLM=1 python -m pytest tests/live -q
"""

import os

import pytest

from investigator.llm.claude_cli import ClaudeCliBackend
from investigator.llm.client import LlmClient, LlmLimits
from investigator.models.common import StrictModel

pytestmark = pytest.mark.skipif(os.environ.get("IV_LIVE_LLM") != "1",
                                reason="live LLM test; set IV_LIVE_LLM=1 to run")


class Sum(StrictModel):
    total: int


class MemoryLog:
    def __init__(self):
        self.rows = []

    async def record(self, row):
        self.rows.append(row)


@pytest.mark.asyncio
async def test_a_real_haiku_call_returns_valid_structured_output():
    log = MemoryLog()
    client = LlmClient(backend=ClaudeCliBackend(timeout_s=120), run_id="live-test", model="haiku",
                       limits=LlmLimits(), log=log, cache=None)
    answer = await client.structured(
        "reasoning",
        "You answer with one JSON object and nothing else.",
        'Add 17 and 25. Reply as {"total": <number>}.',
        Sum,
    )
    assert answer.total == 42
    assert log.rows[-1].status == "ok"
    assert log.rows[-1].input_tokens > 0
