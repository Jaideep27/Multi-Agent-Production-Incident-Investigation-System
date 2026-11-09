"""The agent_ro connection pool under load and after failures."""

import asyncio

import psycopg
import pytest
import pytest_asyncio

from investigator.tools.failures import FailureInjector
from investigator.tools.postgres_backend import PostgresBackend, open_agent_pool
from investigator.tools.service import ToolService
from tests.conftest import admin_settings

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

POOL_MAX = 4


@pytest_asyncio.fixture
async def small_pool(pg_database):
    pool = await open_agent_pool(min_size=1, max_size=POOL_MAX)
    yield pool
    await pool.close()


def make_service(pool, incident_id):
    return ToolService(backend=PostgresBackend(pool, incident_id),
                       injector=FailureInjector(seed=incident_id, rules=[]))


async def count_agent_connections(admin, dbname):
    cursor = await admin.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE usename = 'agent_ro' AND datname = %s",
        (dbname,),
    )
    row = await cursor.fetchone()
    return row[0]


async def test_fifty_calls_on_a_pool_of_four_stay_within_the_pool(pg1, small_pool, pg_database):
    settings = admin_settings()
    admin = await psycopg.AsyncConnection.connect(
        host=settings["host"], port=settings["port"], user=settings["user"],
        password=settings["password"], dbname=pg_database, autocommit=True,
    )
    service = make_service(small_pool, pg1.evidence.incident.incident_id)

    peak = 0
    samples = 0
    done = asyncio.Event()

    async def watch():
        nonlocal peak, samples
        while not done.is_set():
            count = await count_agent_connections(admin, pg_database)
            samples = samples + 1
            if count > peak:
                peak = count
            await asyncio.sleep(0.005)

    watcher = asyncio.create_task(watch())
    calls = []
    for number in range(50):
        calls.append(service.call("query_logs", {"contains": "route", "limit": 200,
                                                 "levels": ["INFO", "WARN"]}))
        calls.append(service.call("query_metrics", {"limit": 100}))
    results = await asyncio.gather(*calls)
    done.set()
    await watcher
    await admin.close()

    statuses = []
    for result in results:
        statuses.append(result.status)
    assert statuses == ["ok"] * 100
    assert samples > 3
    assert 1 <= peak <= POOL_MAX


async def test_a_failed_query_gives_its_connection_back(pg1, pg_database):
    pool = await open_agent_pool(min_size=1, max_size=1)
    try:
        service = make_service(pool, pg1.evidence.incident.incident_id)
        # A NUL byte cannot be sent as text, so the query fails inside the pooled connection.
        broken = await service.call("query_logs", {"contains": "bad\x00text"})
        assert broken.status == "error"
        # With a pool of one, this would wait forever if the connection had leaked.
        healthy = await asyncio.wait_for(service.call("query_logs", {"limit": 1}), timeout=10)
        assert healthy.status == "ok"
    finally:
        await pool.close()


async def test_a_pooled_connection_keeps_no_incident_binding(pg1, pg_database):
    pool = await open_agent_pool(min_size=1, max_size=1)
    try:
        service = make_service(pool, pg1.evidence.incident.incident_id)
        result = await service.call("get_service_dependencies", {})
        assert result.status == "ok"
        async with pool.connection() as connection:
            cursor = await connection.execute("SELECT current_setting('app.incident_id', true)")
            row = await cursor.fetchone()
            assert row[0] in (None, "")
            cursor = await connection.execute("SELECT count(*) FROM evidence.log_entries")
            row = await cursor.fetchone()
            assert row[0] == 0
    finally:
        await pool.close()
