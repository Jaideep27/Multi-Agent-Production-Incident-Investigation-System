"""What every LLM backend returns, and the interface it implements."""

from abc import ABC, abstractmethod

from investigator.models.common import NonNegativeInt, StrictModel


class Completion(StrictModel):
    text: str
    input_tokens: NonNegativeInt      # every prompt token, cached or not
    output_tokens: NonNegativeInt
    latency_ms: NonNegativeInt
    model: str


class LlmBackendError(Exception):
    """The backend ran but gave no usable completion (crash, API error, unreadable output)."""


class LlmBackend(ABC):
    @abstractmethod
    async def complete(self, system, prompt, model):
        """Return a Completion. May raise LlmBackendError or TimeoutError."""
