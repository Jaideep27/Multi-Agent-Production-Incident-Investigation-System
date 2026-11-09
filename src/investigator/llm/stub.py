"""StubBackend: answers from a script instead of a model (tests and quota-free stub runs).

respond(system, prompt, model, call_number) returns the reply text, an exception to raise,
or HANG to never answer (to exercise timeouts). Token counts are rough (4 characters per
token), enough for the cost columns to be non-zero.
"""

import asyncio
import time

from investigator.llm.backend import Completion, LlmBackend

HANG = "hang"


class StubBackend(LlmBackend):
    def __init__(self, respond, latency_s=0.0):
        self.respond = respond
        self.latency_s = latency_s
        self.calls = []          # [system, prompt, model]
        self.in_flight = 0
        self.peak = 0

    async def complete(self, system, prompt, model):
        self.calls.append([system, prompt, model])
        number = len(self.calls)
        self.in_flight = self.in_flight + 1
        if self.in_flight > self.peak:
            self.peak = self.in_flight
        started = time.monotonic()
        try:
            if self.latency_s > 0:
                await asyncio.sleep(self.latency_s)
            answer = self.respond(system, prompt, model, number)
            if isinstance(answer, str) and answer == HANG:
                await asyncio.sleep(3600)
            if isinstance(answer, Exception):
                raise answer
        finally:
            self.in_flight = self.in_flight - 1
        latency_ms = int((time.monotonic() - started) * 1000)
        input_tokens = max(1, (len(system) + len(prompt)) // 4)
        output_tokens = max(1, len(answer) // 4)
        return Completion(text=answer, input_tokens=input_tokens, output_tokens=output_tokens,
                          latency_ms=latency_ms, model=model)
