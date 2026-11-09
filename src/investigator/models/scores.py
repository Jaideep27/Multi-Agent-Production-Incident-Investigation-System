"""One scored run. Mirrors the eval.scores table, including its CHECK constraints, so a bad
score is caught in Python before the INSERT."""

from uuid import UUID

from pydantic import model_validator

from investigator.models.common import NonNegativeInt, Probability, StrictModel


class ScoreRecord(StrictModel):
    run_id: UUID
    root_cause_correct: bool
    outcome_correct: bool
    grounding_cited: NonNegativeInt
    grounding_existing: NonNegativeInt
    grounding_supporting: NonNegativeInt | None = None   # defined in the schema, not used yet
    decisive_recall: Probability | None = None
    decoy_hit: bool
    decoys_cited_as_support: NonNegativeInt = 0
    confidence: Probability | None = None
    tool_call_count: NonNegativeInt
    llm_call_count: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    latency_ms: NonNegativeInt
    rounds: NonNegativeInt

    @model_validator(mode="after")
    def check_grounding(self):
        if self.grounding_existing > self.grounding_cited:
            raise ValueError("grounding_existing cannot exceed grounding_cited")
        return self
