"""LlmClient: ask for a structured answer and get a validated Pydantic object, or a clear failure.

structured(agent, system, prompt, OutputModel) makes at most max_attempts attempts (never more
than 3). An attempt fails when the call times out, the backend errors, or the reply is not a
JSON object that validates against OutputModel (unknown fields included, since agent models
forbid them). After an invalid reply the next attempt sends the original prompt plus the
problem and the bad reply, so the model can repair it. After the last attempt LlmOutputError
is raised; the caller marks the agent's task failed.

Every attempt is one eval.llm_calls row. The run's LLM-call budget counts every attempt,
cache hits included, so a replayed run makes exactly the same calls as the recorded one.
"""

import asyncio
import json
import time
from typing import Literal

from pydantic import Field, ValidationError

from investigator.llm.cache import CacheMiss
from investigator.models.common import NonEmptyText, StrictModel


class LlmLimits(StrictModel):
    max_attempts: int = Field(default=3, ge=1, le=3)       # hard rule: at most 3
    timeout_s: float = Field(default=120.0, gt=0)          # per attempt
    max_concurrency: int = Field(default=2, ge=1, le=8)    # Pro plan: keep this low
    max_llm_calls: int = Field(default=60, ge=1)           # per run, every attempt counts


class LlmCallRow(StrictModel):
    run_id: str
    agent: NonEmptyText
    attempt: int = Field(ge=1)
    status: Literal["ok", "invalid_output", "timeout", "error"]
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int = Field(ge=0)


class LlmOutputError(Exception):
    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = attempts


class LlmBudgetExhausted(Exception):
    pass


def extract_json(text):
    """The first-to-last brace span of the reply, parsed; code fences and prose are ignored."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end < start:
        raise ValueError("the reply contains no JSON object")
    value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("the reply's JSON is not an object")
    return value


def repair_prompt(original_prompt, bad_reply, problem):
    return (
        original_prompt
        + "\n\n---\nYour previous reply could not be used.\n"
        + "Problem:\n" + problem + "\n\n"
        + "Your previous reply was:\n" + bad_reply + "\n\n"
        + "Reply again with exactly one JSON object that fixes the problem. "
        + "No prose and no code fences."
    )


class LlmClient:
    def __init__(self, backend, run_id, model, limits, log, cache=None, transcript_path=None):
        self.backend = backend
        self.run_id = run_id
        self.model = model
        self.limits = limits
        self.log = log
        self.cache = cache
        # Optional JSONL file with every prompt and reply of the run, so the viewer can show
        # how each agent reached its output.
        self.transcript_path = transcript_path
        self.semaphore = asyncio.Semaphore(limits.max_concurrency)
        self.calls_made = 0

    def keep_transcript(self, agent, attempt, status, prompt, reply, problem=None):
        if self.transcript_path is None:
            return
        entry = {"time": time.time(), "agent": agent, "attempt": attempt, "status": status,
                 "prompt": prompt, "reply": reply, "problem": problem}
        with open(self.transcript_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def fetch(self, system, prompt):
        if self.cache is None:
            return await self.backend.complete(system, prompt, self.model)

        async def call_backend():
            return await self.backend.complete(system, prompt, self.model)

        return await self.cache.get_or_call(self.model, system, prompt, call_backend)

    async def record(self, agent, attempt, status, completion, latency_ms):
        input_tokens = None
        output_tokens = None
        if completion is not None:
            input_tokens = completion.input_tokens
            output_tokens = completion.output_tokens
            latency_ms = completion.latency_ms
        await self.log.record(LlmCallRow(run_id=self.run_id, agent=agent, attempt=attempt,
                                         status=status, input_tokens=input_tokens,
                                         output_tokens=output_tokens, latency_ms=latency_ms))

    async def structured(self, agent, system, prompt, output_model, check=None):
        """check(parsed) may return a list of problems (for example evidence IDs the run never
        received); a non-empty list is treated exactly like invalid JSON: logged as
        invalid_output and repaired on the next attempt."""
        current_prompt = prompt
        last_problem = "no attempt made"
        for attempt in range(1, self.limits.max_attempts + 1):
            if self.calls_made >= self.limits.max_llm_calls:
                raise LlmBudgetExhausted("LLM call budget of %d is spent"
                                         % self.limits.max_llm_calls)
            self.calls_made = self.calls_made + 1

            started = time.monotonic()
            try:
                async with self.semaphore:
                    completion = await asyncio.wait_for(self.fetch(system, current_prompt),
                                                        self.limits.timeout_s)
            except CacheMiss:
                raise          # a replay without a recording is a setup problem, not a retry
            except TimeoutError:
                elapsed = int((time.monotonic() - started) * 1000)
                await self.record(agent, attempt, "timeout", None, elapsed)
                last_problem = "timed out after %.0fs" % self.limits.timeout_s
                self.keep_transcript(agent, attempt, "timeout", current_prompt, None, last_problem)
                continue
            except Exception as error:
                elapsed = int((time.monotonic() - started) * 1000)
                await self.record(agent, attempt, "error", None, elapsed)
                last_problem = "%s: %s" % (type(error).__name__, error)
                self.keep_transcript(agent, attempt, "error", current_prompt, None, last_problem)
                continue

            try:
                data = extract_json(completion.text)
                parsed = output_model.model_validate(data)
            except (ValueError, ValidationError) as problem:
                await self.record(agent, attempt, "invalid_output", completion, 0)
                last_problem = str(problem)
                self.keep_transcript(agent, attempt, "invalid_output", current_prompt,
                                     completion.text, last_problem)
                current_prompt = repair_prompt(prompt, completion.text, last_problem)
                continue

            if check is not None:
                problems = check(parsed)
                if problems:
                    await self.record(agent, attempt, "invalid_output", completion, 0)
                    last_problem = "\n".join(problems)
                    self.keep_transcript(agent, attempt, "invalid_output", current_prompt,
                                         completion.text, last_problem)
                    current_prompt = repair_prompt(prompt, completion.text, last_problem)
                    continue

            await self.record(agent, attempt, "ok", completion, 0)
            self.keep_transcript(agent, attempt, "ok", current_prompt, completion.text)
            return parsed

        raise LlmOutputError("%s gave no usable answer after %d attempts; last problem: %s"
                             % (agent, self.limits.max_attempts, last_problem),
                             attempts=self.limits.max_attempts)
