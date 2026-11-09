"""LlmClient: JSON extraction, validation, repair retries (gate: at most 3), limits, cache."""

import asyncio
import json
import time

import pytest
from pydantic import ValidationError

from investigator.llm.cache import CacheMiss, ResponseCache
from investigator.llm.client import (
    LlmBudgetExhausted,
    LlmClient,
    LlmLimits,
    LlmOutputError,
    extract_json,
)
from investigator.llm.stub import HANG, StubBackend
from investigator.models.common import StrictModel

RUN_ID = "00000000-0000-0000-0000-000000000001"


class Answer(StrictModel):
    verdict: str
    score: int


class MemoryLlmLog:
    def __init__(self):
        self.rows = []

    async def record(self, row):
        self.rows.append(row)


def make_client(respond, tmp_path=None, cache_mode="off", latency_s=0.0, **limits):
    settings = {"timeout_s": 5.0}
    for key in limits:
        settings[key] = limits[key]
    backend = StubBackend(respond, latency_s=latency_s)
    cache = None
    if cache_mode != "off":
        cache = ResponseCache(tmp_path / "cache", mode=cache_mode)
    log = MemoryLlmLog()
    client = LlmClient(backend=backend, run_id=RUN_ID, model="haiku", limits=LlmLimits(**settings),
                       log=log, cache=cache)
    return client, backend, log


def statuses(log):
    found = []
    for row in log.rows:
        found.append([row.attempt, row.status])
    return found


def good(system, prompt, model, number):
    return '{"verdict": "ok", "score": 3}'


def always_invalid(system, prompt, model, number):
    return "I think the answer is probably fine."


# ------------------------------------------------------------------ extraction


def test_extract_json_handles_fences_and_surrounding_prose():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go:\n{"a": {"b": [1, 2]}}\nHope that helps.') == {"a": {"b": [1, 2]}}


def test_extract_json_rejects_text_without_an_object():
    with pytest.raises(ValueError):
        extract_json("no json here")
    with pytest.raises(ValueError):
        extract_json("[1, 2, 3]")


# ------------------------------------------------------------------ attempts (the gate)


@pytest.mark.asyncio
async def test_valid_output_takes_one_attempt():
    client, backend, log = make_client(good)
    answer = await client.structured("reasoning", "system", "prompt", Answer)
    assert answer == Answer(verdict="ok", score=3)
    assert statuses(log) == [[1, "ok"]]
    assert log.rows[0].agent == "reasoning"
    assert log.rows[0].input_tokens > 0
    assert log.rows[0].output_tokens > 0


@pytest.mark.asyncio
async def test_invalid_output_is_repaired_on_the_next_attempt():
    def respond(system, prompt, model, number):
        if number == 1:
            return '{"verdict": "ok"}'           # score missing
        return '{"verdict": "ok", "score": 4}'

    client, backend, log = make_client(respond)
    answer = await client.structured("critic", "system", "original question", Answer)
    assert answer.score == 4
    assert statuses(log) == [[1, "invalid_output"], [2, "ok"]]
    repair_prompt = backend.calls[1][1]
    assert "original question" in repair_prompt
    assert "score" in repair_prompt                  # the validation error is shown
    assert '{"verdict": "ok"}' in repair_prompt      # and so is the bad reply


@pytest.mark.asyncio
async def test_every_exchange_is_kept_in_the_run_transcript(tmp_path):
    def respond(system, prompt, model, number):
        if number == 1:
            return '{"verdict": "ok"}'
        return '{"verdict": "ok", "score": 4}'

    client, backend, log = make_client(respond)
    client.transcript_path = tmp_path / "llm_transcript.jsonl"
    await client.structured("critic", "system", "original question", Answer)
    lines = client.transcript_path.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        entries.append(json.loads(line))
    assert [[e["attempt"], e["status"]] for e in entries] == [[1, "invalid_output"], [2, "ok"]]
    assert entries[0]["prompt"] == "original question"
    assert entries[0]["reply"] == '{"verdict": "ok"}'
    assert "score" in entries[0]["problem"]
    assert entries[1]["agent"] == "critic"
    assert entries[1]["reply"] == '{"verdict": "ok", "score": 4}'


@pytest.mark.asyncio
async def test_always_invalid_output_stops_after_exactly_three_attempts():
    client, backend, log = make_client(always_invalid)
    with pytest.raises(LlmOutputError) as caught:
        await client.structured("reporting", "system", "prompt", Answer)
    assert len(backend.calls) == 3
    assert statuses(log) == [[1, "invalid_output"], [2, "invalid_output"], [3, "invalid_output"]]
    assert caught.value.attempts == 3


@pytest.mark.asyncio
async def test_unknown_fields_count_as_invalid():
    def respond(system, prompt, model, number):
        if number == 1:
            return '{"verdict": "ok", "score": 1, "incident_id": "inc_x"}'
        return '{"verdict": "ok", "score": 1}'

    client, backend, log = make_client(respond)
    await client.structured("reasoning", "s", "p", Answer)
    assert statuses(log) == [[1, "invalid_output"], [2, "ok"]]


@pytest.mark.asyncio
async def test_max_attempts_cannot_exceed_three():
    with pytest.raises(ValidationError):
        LlmLimits(max_attempts=4)
    client, backend, log = make_client(always_invalid, max_attempts=2)
    with pytest.raises(LlmOutputError):
        await client.structured("reasoning", "s", "p", Answer)
    assert len(backend.calls) == 2


@pytest.mark.asyncio
async def test_a_call_that_hangs_times_out_and_counts_as_an_attempt():
    def respond(system, prompt, model, number):
        if number == 1:
            return HANG
        return '{"verdict": "ok", "score": 2}'

    client, backend, log = make_client(respond, timeout_s=0.1)
    started = time.monotonic()
    answer = await client.structured("dependency", "s", "p", Answer)
    assert time.monotonic() - started < 2.0
    assert answer.score == 2
    assert statuses(log) == [[1, "timeout"], [2, "ok"]]


@pytest.mark.asyncio
async def test_backend_errors_are_retried_within_the_same_limit():
    client, backend, log = make_client(
        lambda system, prompt, model, number: RuntimeError("cli exited with 1"))
    with pytest.raises(LlmOutputError):
        await client.structured("reasoning", "s", "p", Answer)
    assert statuses(log) == [[1, "error"], [2, "error"], [3, "error"]]


@pytest.mark.asyncio
async def test_a_failed_semantic_check_is_repaired_like_invalid_json():
    def respond(system, prompt, model, number):
        if number == 1:
            return '{"verdict": "made-up", "score": 1}'
        return '{"verdict": "ok", "score": 1}'

    def check(answer):
        if answer.verdict != "ok":
            return ["verdict must be ok, got %s" % answer.verdict]
        return []

    client, backend, log = make_client(respond)
    answer = await client.structured("reasoning", "s", "p", Answer, check=check)
    assert answer.verdict == "ok"
    assert statuses(log) == [[1, "invalid_output"], [2, "ok"]]
    assert "verdict must be ok" in backend.calls[1][1]


# ------------------------------------------------------------------ budget and concurrency


@pytest.mark.asyncio
async def test_llm_call_budget_counts_every_attempt():
    client, backend, log = make_client(always_invalid, max_llm_calls=4)
    with pytest.raises(LlmOutputError):
        await client.structured("reasoning", "s", "p", Answer)     # uses 3
    with pytest.raises(LlmBudgetExhausted):
        await client.structured("reasoning", "s", "p2", Answer)    # 1 left, then refused
    assert len(backend.calls) == 4
    assert client.calls_made == 4


@pytest.mark.asyncio
async def test_concurrent_calls_respect_the_limit():
    client, backend, log = make_client(good, latency_s=0.05, max_concurrency=2)
    calls = []
    for number in range(6):
        calls.append(client.structured("observability", "s", "p%d" % number, Answer))
    await asyncio.gather(*calls)
    assert backend.peak == 2


# ------------------------------------------------------------------ cache


@pytest.mark.asyncio
async def test_record_mode_stores_and_then_reuses_a_response(tmp_path):
    client, backend, log = make_client(good, tmp_path=tmp_path, cache_mode="record")
    await client.structured("reasoning", "s", "p", Answer)
    await client.structured("reasoning", "s", "p", Answer)
    assert len(backend.calls) == 1
    files = list((tmp_path / "cache").glob("*.json"))
    assert len(files) == 1
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["request"]["prompt"] == "p"
    assert stored["completion"]["text"] == '{"verdict": "ok", "score": 3}'
    # a replayed answer is logged with the tokens it originally cost
    assert log.rows[0].input_tokens == log.rows[1].input_tokens


@pytest.mark.asyncio
async def test_replay_mode_never_calls_the_model(tmp_path):
    recorder, backend, log = make_client(good, tmp_path=tmp_path, cache_mode="record")
    await recorder.structured("reasoning", "s", "p", Answer)

    replayer, replay_backend, replay_log = make_client(good, tmp_path=tmp_path, cache_mode="replay")
    answer = await replayer.structured("reasoning", "s", "p", Answer)
    assert answer.score == 3
    assert replay_backend.calls == []
    with pytest.raises(CacheMiss):
        await replayer.structured("reasoning", "s", "a prompt never recorded", Answer)
    assert replay_backend.calls == []


@pytest.mark.asyncio
async def test_repairs_are_cached_too_so_a_replay_repeats_them(tmp_path):
    def respond(system, prompt, model, number):
        if number == 1:
            return "not json"
        return '{"verdict": "ok", "score": 5}'

    recorder, backend, log = make_client(respond, tmp_path=tmp_path, cache_mode="record")
    await recorder.structured("reasoning", "s", "p", Answer)
    replayer, replay_backend, replay_log = make_client(respond, tmp_path=tmp_path,
                                                      cache_mode="replay")
    answer = await replayer.structured("reasoning", "s", "p", Answer)
    assert answer.score == 5
    assert statuses(replay_log) == [[1, "invalid_output"], [2, "ok"]]
    assert replay_backend.calls == []


def test_cache_key_depends_on_model_system_and_prompt(tmp_path):
    cache = ResponseCache(tmp_path, mode="record")
    base = cache.key("haiku", "s", "p")
    assert cache.key("haiku", "s", "p") == base
    assert cache.key("sonnet", "s", "p") != base
    assert cache.key("haiku", "s2", "p") != base
    assert cache.key("haiku", "s", "p2") != base


def test_cache_rejects_an_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        ResponseCache(tmp_path, mode="sometimes")
