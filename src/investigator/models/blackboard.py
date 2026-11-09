"""Rows on the shared blackboard: tasks, facts, hypotheses, objections.

These models check shape only. Whether a cited evidence ID really came back from a tool in
this run is checked when the row is written to the blackboard (step 5), because only the
blackboard knows this run's tool results.
"""

from typing import Literal

from pydantic import Field, model_validator

from investigator.models.common import (
    AgentName,
    EvidenceId,
    NonEmptyText,
    NonNegativeInt,
    Probability,
    RootCauseCategory,
    StrictModel,
    find_duplicates,
)
from investigator.models.tools import ToolRequest

TaskStatus = Literal["pending", "running", "done", "failed"]


class Task(StrictModel):
    task_id: NonEmptyText
    agent: AgentName
    goal: NonEmptyText
    depends_on: list[str] = Field(default_factory=list)
    status: TaskStatus = "pending"
    round: NonNegativeInt

    @model_validator(mode="after")
    def check_dependencies(self):
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        return self


class Fact(StrictModel):
    """Something an agent observed (kind "fact") or assumed (kind "assumption").

    The assignment asks to keep facts and assumptions apart. A fact must cite evidence; an
    assumption may cite none.
    """

    fact_id: NonEmptyText
    kind: Literal["fact", "assumption"]
    statement: NonEmptyText
    evidence_ids: list[EvidenceId] = Field(default_factory=list)
    author: AgentName
    round: NonNegativeInt

    @model_validator(mode="after")
    def check_evidence(self):
        if self.kind == "fact" and len(self.evidence_ids) == 0:
            raise ValueError("a fact must cite at least one evidence id")
        duplicates = find_duplicates(self.evidence_ids)
        if len(duplicates) > 0:
            raise ValueError("duplicate evidence ids: %s" % duplicates)
        return self


class Hypothesis(StrictModel):
    hypothesis_id: NonEmptyText
    component: NonEmptyText
    category: RootCauseCategory
    mechanism: NonEmptyText
    confidence: Probability
    supporting_fact_ids: list[str] = Field(default_factory=list)
    contradicting_fact_ids: list[str] = Field(default_factory=list)
    author: AgentName
    round: NonNegativeInt

    @model_validator(mode="after")
    def check_fact_sides(self):
        for fact_id in self.supporting_fact_ids:
            if fact_id in self.contradicting_fact_ids:
                raise ValueError("fact %s both supports and contradicts" % fact_id)
        return self


class Objection(StrictModel):
    """A Critic challenge. A blocking objection keeps the investigation going; the requested
    tool calls are what the Critic wants checked before it will accept the hypothesis."""

    objection_id: NonEmptyText
    hypothesis_id: str | None = None  # None means it is about the investigation as a whole
    reason: NonEmptyText
    blocking: bool
    requested_tools: list[ToolRequest] = Field(default_factory=list)
    alternative: str | None = None             # the competing explanation the Critic names
    follow_up_agent: str | None = None         # who was asked to settle it
    resolved: bool = False
    round: NonNegativeInt
