"""What each agent must return (validated by LlmClient.structured; unknown fields rejected).

IDs are never chosen by the model: Python mints task, fact, hypothesis and objection IDs when
it writes to the blackboard. Agents refer to existing items by the IDs shown in their prompt.
"""

from typing import Literal

from pydantic import Field, model_validator

from investigator.models.common import (
    EvidenceId,
    NonEmptyText,
    Probability,
    RootCauseCategory,
    StrictModel,
    find_duplicates,
)
from investigator.models.report import FinalReport
from investigator.models.tools import ToolRequest

SpecialistName = Literal["observability", "dependency"]
MAX_MESSAGE_CHARS = 600


class PlannedTask(StrictModel):
    agent: SpecialistName
    goal: NonEmptyText
    # 1-based position of an earlier task in this plan that must finish first; this task
    # then starts from that task's facts (a hand-off between agents).
    after: int | None = Field(default=None, ge=1)


class OrchestratorOutput(StrictModel):
    tasks: list[PlannedTask] = Field(min_length=1, max_length=4)
    rationale: str = ""

    @model_validator(mode="after")
    def check_dependencies(self):
        position = 1
        for task in self.tasks:
            if task.after is not None and not task.after < position:
                raise ValueError("task %d can only wait for an earlier task (after < %d)"
                                 % (position, position))
            position = position + 1
        return self


class ProposedFact(StrictModel):
    kind: Literal["fact", "assumption"]
    statement: NonEmptyText
    evidence_ids: list[EvidenceId] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_evidence(self):
        # A claim with no evidence is an assumption by definition, so say so rather than make
        # the model answer again.
        if self.kind == "fact" and len(self.evidence_ids) == 0:
            self.kind = "assumption"
        if find_duplicates(self.evidence_ids):
            raise ValueError("duplicate evidence ids in one fact")
        return self


class SpecialistOutput(StrictModel):
    tool_requests: list[ToolRequest] = Field(default_factory=list, max_length=4)
    facts: list[ProposedFact] = Field(default_factory=list, max_length=10)
    done: bool = False


class ProposedHypothesis(StrictModel):
    component: NonEmptyText
    category: RootCauseCategory
    mechanism: NonEmptyText
    confidence: Probability
    supporting_fact_ids: list[str] = Field(default_factory=list)
    contradicting_fact_ids: list[str] = Field(default_factory=list)


class ReasoningOutput(StrictModel):
    """Two to four competing hypotheses in rank order, best first."""

    hypotheses: list[ProposedHypothesis] = Field(min_length=2, max_length=4)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_ranking(self):
        def confidence_of(hypothesis):
            return hypothesis.confidence

        self.hypotheses.sort(key=confidence_of, reverse=True)
        seen = []
        for hypothesis in self.hypotheses:
            key = hypothesis.component + "/" + hypothesis.category
            if key in seen:
                raise ValueError("two hypotheses for %s; merge them" % key)
            seen.append(key)
        return self


class ProposedObjection(StrictModel):
    hypothesis_id: str | None = None      # None: about the investigation as a whole
    reason: NonEmptyText
    blocking: bool
    # A blocking objection must be settleable: the competing explanation, and which
    # specialist should run the check that tells the two apart (a follow-up task).
    alternative: str | None = None
    follow_up_agent: SpecialistName | None = None
    requested_tools: list[ToolRequest] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def check_settleable(self):
        if self.blocking:
            if not self.alternative or not self.alternative.strip():
                raise ValueError("a blocking objection must name the competing explanation (alternative)")
            if self.follow_up_agent is None:
                raise ValueError("a blocking objection must name follow_up_agent "
                                 "(observability or dependency) to settle it")
        return self


class CriticOutput(StrictModel):
    """The Critic's verdict: approve the top hypothesis, or object (at most once per
    hypothesis as blocking)."""

    approved: bool
    objections: list[ProposedObjection] = Field(default_factory=list, max_length=5)
    resolved_objection_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_verdict(self):
        if self.approved:
            for objection in self.objections:
                if objection.blocking:
                    raise ValueError("approved=true cannot come with a blocking objection")
        return self


class AgentMessage(StrictModel):
    """One agent speaking directly to another (the agent-to-agent pattern)."""

    to: Literal["orchestrator", "observability", "dependency", "reasoning", "critic"]
    intent: Literal["finding", "question", "challenge", "answer"]
    text: NonEmptyText
    evidence_ids: list[EvidenceId] = Field(default_factory=list)

    @model_validator(mode="after")
    def keep_it_short(self):
        self.text = self.text[:MAX_MESSAGE_CHARS]
        return self


class OrchestratorTurn(OrchestratorOutput):
    messages: list[AgentMessage] = Field(default_factory=list, max_length=2)


class SpecialistTurn(SpecialistOutput):
    messages: list[AgentMessage] = Field(default_factory=list, max_length=2)


class ReasoningTurn(ReasoningOutput):
    messages: list[AgentMessage] = Field(default_factory=list, max_length=2)


class CriticTurn(CriticOutput):
    messages: list[AgentMessage] = Field(default_factory=list, max_length=2)


class BaselineStep(StrictModel):
    """One step of the single-agent baseline: ask for tools, keep notes, or finish."""

    tool_requests: list[ToolRequest] = Field(default_factory=list, max_length=4)
    notes: list[ProposedFact] = Field(default_factory=list, max_length=10)
    final_report: FinalReport | None = None


__all__ = [
    "AgentMessage",
    "BaselineStep",
    "CriticOutput",
    "CriticTurn",
    "FinalReport",
    "OrchestratorOutput",
    "OrchestratorTurn",
    "ReasoningTurn",
    "SpecialistTurn",
    "PlannedTask",
    "ProposedFact",
    "ProposedHypothesis",
    "ProposedObjection",
    "ReasoningOutput",
    "SpecialistOutput",
]
