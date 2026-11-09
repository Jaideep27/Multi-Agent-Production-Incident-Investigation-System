"""ToolService: argument validation, attempt counting, failure injection and the envelope."""

import asyncio
import random
import time

import pytest

from investigator.models.tools import ToolResult
from investigator.tools.failures import FailureInjector
from investigator.tools.service import ToolService, UnknownToolError
from tests.fakes import FakeBackend

SEED = "inc_0000000a"


def make_service(rules=None, backend=None, latency_ms=0):
    if backend is None:
        backend = FakeBackend()
    injector = FailureInjector(seed=SEED, rules=rules or [])
    return ToolService(backend=backend, injector=injector, latency_ms=latency_ms), backend


# ------------------------------------------------------------------ envelope


@pytest.mark.asyncio
async def test_ok_result_carries_the_backend_items():
    service, backend = make_service()
    result = await service.call("query_logs", {"service": "api"})
    assert isinstance(result, ToolResult)
    assert result.status == "ok"
    assert result.tool == "query_logs"
    assert len(result.items) == 6
    assert backend.calls[0][0] == "query_logs"


@pytest.mark.asyncio
async def test_no_items_means_status_empty():
    service, backend = make_service()
    result = await service.call("search_past_incidents", {"query": "timeouts"})
    assert result.status == "empty"
    assert result.items == []


@pytest.mark.asyncio
async def test_every_call_gets_its_own_call_id():
    service, backend = make_service()
    ids = []
    for number in range(5):
        result = await service.call("query_logs", {})
        ids.append(result.call_id)
    assert len(ids) == 5
    for call_id in ids:
        assert ids.count(call_id) == 1


@pytest.mark.asyncio
async def test_unknown_tool_raises():
    service, backend = make_service()
    with pytest.raises(UnknownToolError):
        await service.call("drop_tables", {})


# ------------------------------------------------------------------ validation


@pytest.mark.asyncio
async def test_invalid_arguments_return_an_error_without_touching_the_backend():
    service, backend = make_service()
    result = await service.call("query_logs", {"limit": 0})
    assert result.status == "error"
    assert "invalid arguments" in result.error
    assert backend.calls == []


@pytest.mark.asyncio
async def test_incident_id_is_not_an_accepted_argument():
    service, backend = make_service()
    result = await service.call("query_logs", {"service": "api", "incident_id": "inc_0000000b"})
    assert result.status == "error"
    assert "incident_id" in result.error
    assert backend.calls == []


@pytest.mark.asyncio
async def test_backend_receives_normalised_arguments():
    service, backend = make_service()
    await service.call("query_logs", {"start": "2026-10-01T10:00:00+02:00"})
    tool, args = backend.calls[0]
    assert args["start"] == "2026-10-01T08:00:00Z"
    assert args["limit"] == 50


@pytest.mark.asyncio
async def test_backend_exception_becomes_an_error_result():
    service, backend = make_service(backend=FakeBackend(error=RuntimeError("socket closed")))
    result = await service.call("query_logs", {})
    assert result.status == "error"
    assert "RuntimeError" in result.error


# ------------------------------------------------------------------ attempts and injection


@pytest.mark.asyncio
async def test_transient_failure_then_success_on_retry():
    service, backend = make_service(rules=[["query_logs", {"mode": "transient", "fail_first_n": 1}]])
    first = await service.call("query_logs", {"service": "api"})
    second = await service.call("query_logs", {"service": "api"})
    assert first.status == "timeout"
    assert first.error
    assert first.items == []
    assert second.status == "ok"
    # A failed attempt never reached the backend.
    assert len(backend.calls) == 1


@pytest.mark.asyncio
async def test_attempts_are_counted_per_normalised_call():
    # Writing the default limit explicitly is the same call, so it is the second attempt.
    service, backend = make_service(rules=[["query_logs", {"mode": "transient", "fail_first_n": 1}]])
    first = await service.call("query_logs", {"service": "api"})
    same_call = await service.call("query_logs", {"service": "api", "limit": 50})
    other_call = await service.call("query_logs", {"service": "db"})
    assert first.status == "timeout"
    assert same_call.status == "ok"
    assert other_call.status == "timeout"


@pytest.mark.asyncio
async def test_persistent_failure_never_succeeds():
    service, backend = make_service(rules=[["get_change_history",
                                            {"mode": "persistent", "status": "error"}]])
    for attempt in range(4):
        result = await service.call("get_change_history", {})
        assert result.status == "error"
    assert backend.calls == []


@pytest.mark.asyncio
async def test_partial_result_drops_items_and_says_so():
    service, backend = make_service(rules=[["query_logs", {"mode": "partial", "keep_fraction": 0.5}]])
    result = await service.call("query_logs", {})
    assert result.status == "partial"
    assert result.warnings
    assert len(result.items) < 6


@pytest.mark.asyncio
async def test_outcomes_do_not_depend_on_call_order():
    rules = [["query_logs", {"mode": "probabilistic", "p": 0.5}]]
    calls = []
    for number in range(30):
        for repeat in range(3):
            calls.append({"contains": "w%d" % number})

    async def run(order):
        service, backend = make_service(rules=rules)
        seen = {}
        for args in order:
            result = await service.call("query_logs", args)
            key = args["contains"]
            if key not in seen:
                seen[key] = []
            seen[key].append(result.status)
        return seen

    shuffled = list(calls)
    random.Random(3).shuffle(shuffled)
    assert await run(calls) == await run(shuffled)


# ------------------------------------------------------------------ latency


@pytest.mark.asyncio
async def test_simulated_latency_runs_concurrently():
    service, backend = make_service(latency_ms=200)
    started = time.monotonic()
    calls = []
    for number in range(10):
        calls.append(service.call("query_logs", {"contains": "w%d" % number}))
    results = await asyncio.gather(*calls)
    elapsed = time.monotonic() - started
    assert len(results) == 10
    assert elapsed < 1.0     # the sum of latencies is 2.0 s
    assert elapsed >= 0.2
