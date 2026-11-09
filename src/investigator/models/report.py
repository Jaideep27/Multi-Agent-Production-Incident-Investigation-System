"""The final investigation report.

The fields follow the assignment (section 2.D): ranked hypotheses, supporting and
contradicting evidence, confidence, recommended actions, facts kept apart from assumptions,
and an inconclusive outcome when the evidence is not enough.
"""

from typing import Literal

from pydantic import Field, model_validator

from investigator.models.common import (
    EvidenceId,
    NonEmptyText,
    Probability,
    RootCauseCategory,
    StrictModel,
)


class ReportFact(StrictModel):
    statement: NonEmptyText
    evidence_ids: list[EvidenceId] = Field(min_length=1)


class ReportHypothesis(StrictModel):
    rank: int = Field(ge=1)
    component: NonEmptyText
    category: RootCauseCategory
    mechanism: NonEmptyText
    confidence: Probability
    confidence_reason: str = ""      # in the agent's words, what makes it this high or low
    supporting_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    contradicting_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    assumptions: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_evidence_sides(self):
        for evidence_id in self.supporting_evidence_ids:
            if evidence_id in self.contradicting_evidence_ids:
                raise ValueError("%s both supports and contradicts" % evidence_id)
        return self


class RecommendedAction(StrictModel):
    action: NonEmptyText
    priority: Literal["immediate", "follow_up", "preventive"]
    rationale: NonEmptyText
    evidence_ids: list[EvidenceId] = Field(default_factory=list)


class FinalReport(StrictModel):
    outcome: Literal["root_cause_identified", "inconclusive"]
    summary: NonEmptyText
    hypotheses: list[ReportHypothesis] = Field(default_factory=list)
    facts: list[ReportFact] = Field(default_factory=list)
    assumptions: list[NonEmptyText] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    unresolved: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_ranking(self):
        # Ranks run 1, 2, 3, ... in list order, and confidence never goes up as rank goes
        # down, so "ranked" means the same thing in every report.
        expected_rank = 1
        previous_confidence = None
        for hypothesis in self.hypotheses:
            if hypothesis.rank != expected_rank:
                raise ValueError(
                    "hypothesis ranks must be 1..n in order; got %d where %d was expected"
                    % (hypothesis.rank, expected_rank)
                )
            if previous_confidence is not None and hypothesis.confidence > previous_confidence:
                raise ValueError("confidence must not increase from one rank to the next")
            previous_confidence = hypothesis.confidence
            expected_rank = expected_rank + 1
        return self

    @model_validator(mode="after")
    def check_outcome(self):
        if self.outcome == "root_cause_identified":
            if len(self.hypotheses) == 0:
                raise ValueError("an identified root cause needs at least one hypothesis")
            if len(self.recommended_actions) == 0:
                raise ValueError("an identified root cause needs at least one recommended action")
        else:
            # Inconclusive is a valid answer, but it has to say what evidence was missing.
            if len(self.unresolved) == 0:
                raise ValueError("an inconclusive report must list what is unresolved")
        return self
