"""The ToolGateway: parallelism, retries, de-duplication, budgets, deadline and logging.

Every limit in GatewayLimits has at least one test here that hits it.
"""

import asyncio
import time

import pytest
from pydantic import ValidationError

from investigator.gateway.gateway import Gateway, GatewayLimits
from investigator.models.tools import ToolRequest
from tests.fakes import HANG, MemoryCallLog, ScriptedCaller, failed_result, ok_result

RUN_ID = "00000000-0000-0000-0000-000000000001"


def request(tool="query_logs", **args):
    return ToolRequest(tool=tool, args=args)


def make_gateway(caller, **limits):
    settings = {"retry_delay_s": 0.0}
    for key in limits:
        settings[key] = limits[key]
    log = MemoryCallLog()
    gateway = Gateway(caller=caller, run_id=RUN_ID, limits=GatewayLimits(**settings), log=log)
    return gateway, log


def always(status):
    def respond(tool, args, attempt):
        return failed_result(tool, status)
    return respond


# ------------------------------------------------------------------ limits model


def test_limits_never_allow_more_than_three_attempts():
    with pytest.raises(ValidationError):
        GatewayLimits(max_attempts=4)


def test_limits_reject_unknown_fields():
    with pytest.raises(ValidationError):
        GatewayLimits(max_retries=2)


# ------------------------------------------------------------------ parallelism


@pytest.mark.asyncio
async def test_independent_calls_run_in_parallel():
    caller = ScriptedCaller(latency_s=0.2)
    gateway, log = make_gateway(caller, max_concurrency=10)
    requests = []
    for number in range(10):
        requests.append(request(contains="w%d" % number))
    started = time.monotonic()
    outcomes = await gateway.execute(requests)
    elapsed = time.monotonic() - started
    assert len(outcomes) == 10
    assert elapsed < 0.6          # serial would be 2.0 s
    assert caller.peak == 10


@pytest.mark.asyncio
async def test_concurrency_never_exceeds_the_limit():
    caller = ScriptedCaller(latency_s=0.05)
    gateway, log = make_gateway(caller, max_concurrency=3)
    requests = []
    for number in range(12):
        requests.append(request(contains="w%d" % number))
    await gateway.execute(requests)
    assert caller.peak == 3
    assert len(caller.calls) == 12


@pytest.mark.asyncio
async def test_outcomes_come_back_in_request_order():
    def respond(tool, args, attempt):
        number = int(args["contains"][1:])
        return ok_result(tool, ["L-0000000a-%d" % number])

    caller = ScriptedCaller(respond=respond, latency_s=0.01)
    gateway, log = make_gateway(caller)
    requests = []
    for number in range(8):
        requests.append(request(contains="w%d" % number))
    outcomes = await gateway.execute(requests)
    for number in range(8):
        assert outcomes[number].request == requests[number]
        assert outcomes[number].result.items[0].evidence_id == "L-0000000a-%d" % number


# ------------------------------------------------------------------ retries


@pytest.mark.asyncio
async def test_persistent_timeout_stops_after_exactly_three_attempts():
    caller = ScriptedCaller(respond=always("timeout"))
    gateway, log = make_gateway(caller)
    outcomes = await gateway.execute([request(service="api")])
    outcome = outcomes[0]
    assert outcome.source == "executed"
    assert outcome.attempts == 3
    assert outcome.result.status == "timeout"
    assert len(caller.calls) == 3
    attempts = []
    for row in log.rows:
        attempts.append([row.attempt, row.status])
    assert attempts == [[1, "timeout"], [2, "timeout"], [3, "timeout"]]


@pytest.mark.asyncio
async def test_persistent_error_also_stops_at_three():
    caller = ScriptedCaller(respond=always("error"))
    gateway, log = make_gateway(caller)
    outcomes = await gateway.execute([request()])
    assert outcomes[0].attempts == 3
    assert len(caller.calls) == 3


@pytest.mark.asyncio
async def test_max_attempts_setting_is_respected():
    caller = ScriptedCaller(respond=always("timeout"))
    gateway, log = make_gateway(caller, max_attempts=2)
    outcomes = await gateway.execute([request()])
    assert outcomes[0].attempts == 2
    assert len(caller.calls) == 2


@pytest.mark.asyncio
async def test_transient_failure_is_retried_until_it_passes():
    def respond(tool, args, attempt):
        if attempt == 1:
            return failed_result(tool, "timeout")
        return ok_result(tool, ["L-0000000a-1"])

    caller = ScriptedCaller(respond=respond)
    gateway, log = make_gateway(caller)
    outcomes = await gateway.execute([request()])
    assert outcomes[0].attempts == 2
    assert outcomes[0].result.status == "ok"
    statuses = []
    for row in log.rows:
        statuses.append(row.status)
    assert statuses == ["timeout", "ok"]


@pytest.mark.asyncio
async def test_partial_and_empty_are_never_retried():
    for status in ["partial", "empty"]:
        caller = ScriptedCaller(respond=always(status))
        gateway, log = make_gateway(caller)
        outcomes = await gateway.execute([request()])
        assert outcomes[0].attempts == 1, status
        assert outcomes[0].result.status == status
        assert len(caller.calls) == 1


@pytest.mark.asyncio
async def test_a_call_that_never_answers_times_out_and_is_retried():
    caller = ScriptedCaller(respond=lambda tool, args, attempt: HANG)
    gateway, log = make_gateway(caller, call_timeout_s=0.1)
    started = time.monotonic()
    outcomes = await gateway.execute([request()])
    elapsed = time.monotonic() - started
    assert outcomes[0].result.status == "timeout"
    assert "gateway" in outcomes[0].result.error
    assert outcomes[0].attempts == 3
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_a_transport_exception_becomes_an_error_and_is_retried():
    caller = ScriptedCaller(respond=lambda tool, args, attempt: ConnectionResetError("pipe closed"))
    gateway, log = make_gateway(caller)
    outcomes = await gateway.execute([request()])
    assert outcomes[0].result.status == "error"
    assert "ConnectionResetError" in outcomes[0].result.error
    assert outcomes[0].attempts == 3


@pytest.mark.asyncio
async def test_retries_wait_between_attempts():
    caller = ScriptedCaller(respond=always("timeout"))
    gateway, log = make_gateway(caller, retry_delay_s=0.1)
    started = time.monotonic()
    await gateway.execute([request()])
    # waits 0.1 s after attempt 1 and 0.2 s after attempt 2
    assert time.monotonic() - started >= 0.3


# ------------------------------------------------------------------ de-duplication


@pytest.mark.asyncio
async def test_identical_calls_in_flight_share_one_execution():
    caller = ScriptedCaller(latency_s=0.1)
    gateway, log = make_gateway(caller)
    # The same call written two ways: the default limit spelled out or not.
    first = request(service="api")
    second = request(service="api", limit=50)
    outcomes = await gateway.execute([first, second])
    assert len(caller.calls) == 1
    sources = [outcomes[0].source, outcomes[1].source]
    assert sorted(sources) == ["executed", "shared_in_flight"]
    assert outcomes[0].result == outcomes[1].result


@pytest.mark.asyncio
async def test_a_repeated_successful_call_is_answered_from_the_cache():
    caller = ScriptedCaller()
    gateway, log = make_gateway(caller)
    await gateway.execute([request(service="api")])
    outcomes = await gateway.execute([request(service="api")])
    assert outcomes[0].source == "cached"
    assert outcomes[0].attempts == 0
    assert len(caller.calls) == 1
    assert len(log.rows) == 1


@pytest.mark.asyncio
async def test_a_failed_call_is_not_cached():
    caller = ScriptedCaller(respond=always("timeout"))
    gateway, log = make_gateway(caller)
    await gateway.execute([request()])
    outcomes = await gateway.execute([request()])
    assert outcomes[0].source == "executed"
    assert len(caller.calls) == 6


# ------------------------------------------------------------------ budget and deadline


@pytest.mark.asyncio
async def test_tool_call_budget_stops_new_calls():
    caller = ScriptedCaller()
    gateway, log = make_gateway(caller, max_tool_calls=3)
    requests = []
    for number in range(5):
        requests.append(request(contains="w%d" % number))
    outcomes = await gateway.execute(requests)
    sources = []
    for outcome in outcomes:
        sources.append(outcome.source)
    assert sources.count("executed") == 3
    assert sources.count("budget_exhausted") == 2
    for outcome in outcomes:
        if outcome.source == "budget_exhausted":
            assert outcome.result is None
    assert len(caller.calls) == 3
    assert gateway.logical_calls == 3


@pytest.mark.asyncio
async def test_duplicates_and_cache_hits_do_not_use_budget():
    caller = ScriptedCaller()
    gateway, log = make_gateway(caller, max_tool_calls=1)
    outcomes = await gateway.execute([request(service="api"), request(service="api")])
    later = await gateway.execute([request(service="api")])
    assert outcomes[0].result is not None
    assert outcomes[1].result is not None
    assert later[0].source == "cached"
    assert gateway.logical_calls == 1


@pytest.mark.asyncio
async def test_retries_count_as_one_logical_call():
    caller = ScriptedCaller(respond=always("timeout"))
    gateway, log = make_gateway(caller, max_tool_calls=1)
    outcomes = await gateway.execute([request()])
    assert outcomes[0].attempts == 3
    assert gateway.logical_calls == 1
    assert gateway.physical_attempts == 3


@pytest.mark.asyncio
async def test_calls_after_the_deadline_are_refused():
    caller = ScriptedCaller()
    gateway, log = make_gateway(caller, deadline_s=0.05)
    await asyncio.sleep(0.1)
    outcomes = await gateway.execute([request()])
    assert outcomes[0].source == "deadline_exceeded"
    assert outcomes[0].result is None
    assert caller.calls == []


@pytest.mark.asyncio
async def test_no_retry_starts_after_the_deadline():
    caller = ScriptedCaller(respond=always("timeout"), latency_s=0.1)
    gateway, log = make_gateway(caller, deadline_s=0.15)
    outcomes = await gateway.execute([request()])
    # attempt 1 ends at ~0.1 s, attempt 2 at ~0.2 s; attempt 3 would start after the deadline
    assert outcomes[0].attempts == 2
    assert outcomes[0].result.status == "timeout"


# ------------------------------------------------------------------ grounding and logging


@pytest.mark.asyncio
async def test_seen_evidence_ids_come_only_from_returned_items():
    def respond(tool, args, attempt):
        if args["contains"] == "good":
            return ok_result(tool, ["L-0000000a-1", "L-0000000a-2"])
        return failed_result(tool, "error")

    caller = ScriptedCaller(respond=respond)
    gateway, log = make_gateway(caller)
    await gateway.execute([request(contains="good"), request(contains="bad")])
    assert gateway.seen_evidence_ids() == ["L-0000000a-1", "L-0000000a-2"]


@pytest.mark.asyncio
async def test_calls_made_lists_each_logical_call_once_with_its_final_status():
    def respond(tool, args, attempt):
        if args.get("contains") == "bad":
            return failed_result(tool, "timeout")
        return ok_result(tool, ["L-0000000a-1", "L-0000000a-2"])

    gateway, log = make_gateway(ScriptedCaller(respond=respond))
    await gateway.execute([request(contains="good"), request(contains="bad")])
    await gateway.execute([request(contains="good")])       # cache hit: not listed again
    made = gateway.calls_made()
    assert len(made) == 2
    by_text = {}
    for tool, args, status, count in made:
        by_text[args["contains"]] = [tool, status, count]
    assert by_text == {"good": ["query_logs", "ok", 2], "bad": ["query_logs", "timeout", 0]}


@pytest.mark.asyncio
async def test_each_attempt_is_logged_with_its_details():
    caller = ScriptedCaller()
    gateway, log = make_gateway(caller)
    await gateway.execute([request(service="api")])
    row = log.rows[0]
    assert row.run_id == RUN_ID
    assert row.tool == "query_logs"
    assert row.args["service"] == "api"
    assert row.args["limit"] == 50
    assert row.attempt == 1
    assert row.status == "ok"
    assert row.latency_ms >= 0
    assert row.evidence_ids == ["L-0000000a-1"]
