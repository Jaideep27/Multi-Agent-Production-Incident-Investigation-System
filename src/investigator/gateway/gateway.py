"""Gateway: runs validated ToolRequests for one run, within fixed limits.

For each request, in this order:
1. a successful earlier answer to the same normalised call is reused ("cached");
2. an identical call already running is awaited instead of started again ("shared_in_flight");
3. past the deadline, or with the tool-call budget spent, the request is refused and gets no
   result ("deadline_exceeded" / "budget_exhausted");
4. otherwise it runs ("executed"): each attempt holds one semaphore slot and is cut off by
   asyncio.wait_for; timeout and error are retried, up to max_attempts (never more than 3);
   ok, partial and empty are final and never retried.

Every physical attempt is logged. Budget counts logical calls: a call and its retries use one
unit; cache hits and shared calls use none.
"""

import asyncio
import time
from typing import Literal

from pydantic import Field

from investigator.models.common import NonNegativeInt, StrictModel, ToolName, ToolStatus
from investigator.models.tools import ToolRequest, ToolResult

RETRYABLE_STATUSES = ["timeout", "error"]


class GatewayLimits(StrictModel):
    max_tool_calls: int = Field(default=40, ge=1)          # logical calls per run
    max_attempts: int = Field(default=3, ge=1, le=3)       # hard rule: at most 3
    max_concurrency: int = Field(default=4, ge=1, le=32)   # attempts running at once
    call_timeout_s: float = Field(default=20.0, gt=0)      # per attempt
    retry_delay_s: float = Field(default=0.25, ge=0)       # wait = delay x attempts so far
    deadline_s: float | None = Field(default=None, gt=0)   # wall clock for the whole run


class CallLogRow(StrictModel):
    run_id: str
    tool: ToolName
    args: dict
    attempt: int = Field(ge=1)
    status: ToolStatus
    latency_ms: NonNegativeInt
    evidence_ids: list[str]


OutcomeSource = Literal[
    "executed", "shared_in_flight", "cached", "budget_exhausted", "deadline_exceeded"
]


class GatewayOutcome(StrictModel):
    request: ToolRequest
    source: OutcomeSource
    attempts: NonNegativeInt          # physical attempts made for this outcome
    result: ToolResult | None = None  # None only when the request was refused

    def result_evidence_ids(self):
        ids = []
        if self.result is not None:
            for item in self.result.items:
                ids.append(item.evidence_id)
        return ids


def evidence_ids_of(result):
    ids = []
    for item in result.items:
        ids.append(item.evidence_id)
    return ids


class Gateway:
    def __init__(self, caller, run_id, limits, log):
        self.caller = caller
        self.run_id = run_id
        self.limits = limits
        self.log = log
        self.semaphore = asyncio.Semaphore(limits.max_concurrency)
        self.started = time.monotonic()
        self.in_flight = {}     # fingerprint -> asyncio.Task giving (result, attempts)
        self.cache = {}         # fingerprint -> final ToolResult (ok, partial or empty)
        self.seen = []          # evidence IDs returned to this run, first-seen order
        self.made = []          # [tool, args, final status, item count] per logical call
        self.logical_calls = 0
        self.physical_attempts = 0
        self.synthetic_count = 0

    # ------------------------------------------------------------------ public

    async def execute(self, requests):
        """Run a batch in parallel; outcomes come back in the order of the requests."""
        coroutines = []
        for request in requests:
            coroutines.append(self.run_one(request))
        outcomes = await asyncio.gather(*coroutines)
        return list(outcomes)

    def calls_made(self):
        return list(self.made)

    def seen_evidence_ids(self):
        """Evidence IDs this run has actually received (the grounding check uses these)."""
        return list(self.seen)

    # ------------------------------------------------------------------ one request

    async def run_one(self, request):
        # Everything up to the first await runs without interruption, so two identical
        # requests in one batch cannot both start executing.
        fingerprint = request.fingerprint()

        if fingerprint in self.cache:
            return GatewayOutcome(request=request, source="cached", attempts=0,
                                  result=self.cache[fingerprint])

        if fingerprint in self.in_flight:
            result, attempts = await self.in_flight[fingerprint]
            return GatewayOutcome(request=request, source="shared_in_flight", attempts=0,
                                  result=result)

        if self.deadline_passed():
            return GatewayOutcome(request=request, source="deadline_exceeded", attempts=0)

        if self.logical_calls >= self.limits.max_tool_calls:
            return GatewayOutcome(request=request, source="budget_exhausted", attempts=0)

        self.logical_calls = self.logical_calls + 1
        task = asyncio.ensure_future(self.execute_with_retries(request, fingerprint))
        self.in_flight[fingerprint] = task
        result, attempts = await task
        return GatewayOutcome(request=request, source="executed", attempts=attempts,
                              result=result)

    async def execute_with_retries(self, request, fingerprint):
        try:
            result = None
            attempts = 0
            while attempts < self.limits.max_attempts:
                if attempts > 0:
                    if self.deadline_passed():
                        break
                    delay = self.limits.retry_delay_s * attempts
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if self.deadline_passed():
                        break
                attempts = attempts + 1
                result = await self.attempt_once(request, attempts)
                if result.status not in RETRYABLE_STATUSES:
                    break

            # One entry per logical call, so agents can see what was already tried.
            self.made.append([request.tool, request.args, result.status, len(result.items)])
            if result.status not in RETRYABLE_STATUSES:
                self.cache[fingerprint] = result
                for evidence_id in evidence_ids_of(result):
                    if evidence_id not in self.seen:
                        self.seen.append(evidence_id)
            return result, attempts
        finally:
            del self.in_flight[fingerprint]

    async def attempt_once(self, request, attempt):
        async with self.semaphore:
            started = time.monotonic()
            timeout = self.limits.call_timeout_s
            left = self.time_left()
            if left is not None and left < timeout:
                timeout = max(left, 0.001)
            try:
                result = await asyncio.wait_for(self.caller.call(request.tool, request.args),
                                                timeout)
            except TimeoutError:
                result = self.synthetic(request.tool, "timeout",
                                        "gateway gave up waiting after %.1fs" % timeout)
            except Exception as error:
                result = self.synthetic(request.tool, "error",
                                        "gateway could not complete the call: %s: %s"
                                        % (type(error).__name__, error))
            latency_ms = int((time.monotonic() - started) * 1000)

        self.physical_attempts = self.physical_attempts + 1
        await self.log.record(CallLogRow(
            run_id=self.run_id, tool=request.tool, args=request.args, attempt=attempt,
            status=result.status, latency_ms=latency_ms, evidence_ids=evidence_ids_of(result),
        ))
        return result

    # ------------------------------------------------------------------ helpers

    def time_left(self):
        if self.limits.deadline_s is None:
            return None
        return self.limits.deadline_s - (time.monotonic() - self.started)

    def deadline_passed(self):
        left = self.time_left()
        return left is not None and left <= 0

    def synthetic(self, tool, status, message):
        """A result the gateway writes itself when the server gave none."""
        self.synthetic_count = self.synthetic_count + 1
        return ToolResult(call_id="gateway-%06d" % self.synthetic_count, tool=tool,
                          status=status, error=message)
