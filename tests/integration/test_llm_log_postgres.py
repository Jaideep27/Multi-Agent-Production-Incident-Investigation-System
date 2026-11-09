"""LLM attempts are logged to eval.llm_calls by harness_rw, one row per attempt."""

import uuid

import psycopg
import pytest
import pytest_asyncio

from investigator import db
from investigator.llm.call_log import PostgresLlmCallLog
from investigator.llm.client import LlmClient, LlmLimits, LlmOutputError
from investigator.llm.stub import StubBackend
from investigator.models.common import StrictModel
from tests.db_seed import INCIDENT_A

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


class Answer(StrictModel):
    score: int


@pytest_asyncio.fixture
async def harness(pg_database):
    connection = await psycopg.AsyncConnection.connect(db.conninfo_for("harness_rw"),
                                                       autocommit=True)
    yield connection
    await connection.close()


@pytest.fixture(autouse=True)
def remove_test_runs(admin_conn):
    yield
    admin_conn.execute("DELETE FROM eval.runs WHERE model = 'llm-log-test'")


async def test_each_attempt_becomes_one_row(harness):
    run_id = str(uuid.uuid4())
    await harness.execute(
        "INSERT INTO eval.runs (run_id, incident_id, system, model, status) "
        "VALUES (%s, %s, 'multi_agent', 'llm-log-test', 'running')",
        (run_id, INCIDENT_A),
    )

    def respond(system, prompt, model, number):
        return "not json at all"

    client = LlmClient(backend=StubBackend(respond), run_id=run_id, model="haiku",
                       limits=LlmLimits(), log=PostgresLlmCallLog(harness), cache=None)
    with pytest.raises(LlmOutputError):
        await client.structured("critic", "s", "p", Answer)

    cursor = await harness.execute(
        "SELECT agent, attempt, status, input_tokens, output_tokens, latency_ms "
        "FROM eval.llm_calls WHERE run_id = %s ORDER BY call_id",
        (run_id,),
    )
    rows = await cursor.fetchall()
    attempts = []
    for agent, attempt, status, input_tokens, output_tokens, latency_ms in rows:
        assert agent == "critic"
        assert input_tokens > 0
        assert latency_ms >= 0
        attempts.append([attempt, status])
    assert attempts == [[1, "invalid_output"], [2, "invalid_output"], [3, "invalid_output"]]
