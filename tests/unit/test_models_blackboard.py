"""Tests for the blackboard row models (investigator.models.blackboard).

These rows are what agents write. The checks here are shape rules only; grounding against
this run's tool results happens when a row is written to the blackboard (step 5).
"""

import pytest
from pydantic import ValidationError

from investigator.models.blackboard import Fact, Hypothesis, Objection, Task


def make_fact(**fields):
    base = {
        "fact_id": "F1",
        "kind": "fact",
        "statement": "api error rate rose from 0.1% to 9% at 10:02",
        "evidence_ids": ["M-aaaaaaaa-1"],
        "author": "observability",
        "round": 1,
    }
    for key in fields:
        base[key] = fields[key]
    return Fact.model_validate(base)


def make_hypothesis(**fields):
    base = {
        "hypothesis_id": "H1",
        "component": "payment-router",
        "category": "config_change",
        "mechanism": "connection pool shrunk, requests queue for a connection",
        "confidence": 0.6,
        "supporting_fact_ids": ["F1"],
        "contradicting_fact_ids": [],
        "author": "reasoning",
        "round": 1,
    }
    for key in fields:
        base[key] = fields[key]
    return Hypothesis.model_validate(base)


# --------------------------------------------------------------------------- facts


def test_fact_with_evidence_is_valid():
    assert make_fact().evidence_ids == ["M-aaaaaaaa-1"]


def test_fact_must_cite_evidence():
    with pytest.raises(ValidationError):
        make_fact(evidence_ids=[])


def test_assumption_may_cite_nothing():
    # The assignment asks to keep facts and assumptions apart; an assumption is allowed
    # to be unsupported, a fact is not.
    assumption = make_fact(kind="assumption", evidence_ids=[])
    assert assumption.kind == "assumption"


def test_fact_rejects_malformed_evidence_ids():
    with pytest.raises(ValidationError):
        make_fact(evidence_ids=["metrics-dashboard-1"])


def test_fact_rejects_duplicate_evidence_ids():
    with pytest.raises(ValidationError):
        make_fact(evidence_ids=["M-aaaaaaaa-1", "M-aaaaaaaa-1"])


def test_fact_rejects_unknown_author():
    with pytest.raises(ValidationError):
        make_fact(author="scorer")


def test_fact_rejects_blank_statement():
    with pytest.raises(ValidationError):
        make_fact(statement="   ")


# ---------------------------------------------------------------------- hypotheses


def test_hypothesis_is_valid():
    assert make_hypothesis().category == "config_change"


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_hypothesis_confidence_is_a_probability(confidence):
    with pytest.raises(ValidationError):
        make_hypothesis(confidence=confidence)


def test_hypothesis_category_must_be_known():
    with pytest.raises(ValidationError):
        make_hypothesis(category="gremlins")


def test_a_fact_cannot_both_support_and_contradict_a_hypothesis():
    with pytest.raises(ValidationError):
        make_hypothesis(supporting_fact_ids=["F1"], contradicting_fact_ids=["F1"])


# --------------------------------------------------------------------------- tasks


def test_task_is_valid():
    task = Task.model_validate(
        {"task_id": "T2", "agent": "dependency", "goal": "list recent deploys",
         "depends_on": ["T1"], "round": 0}
    )
    assert task.status == "pending"


def test_task_cannot_depend_on_itself():
    with pytest.raises(ValidationError):
        Task.model_validate(
            {"task_id": "T1", "agent": "dependency", "goal": "x", "depends_on": ["T1"], "round": 0}
        )


def test_task_status_must_be_known():
    with pytest.raises(ValidationError):
        Task.model_validate(
            {"task_id": "T1", "agent": "dependency", "goal": "x", "status": "maybe", "round": 0}
        )


# ---------------------------------------------------------------------- objections


def test_objection_can_request_more_tool_calls():
    objection = Objection.model_validate(
        {
            "objection_id": "O1",
            "hypothesis_id": "H1",
            "reason": "the pool change is not shown to precede the errors",
            "blocking": True,
            "requested_tools": [
                {"tool": "get_change_history", "args": {"service": "payment-router"}}
            ],
            "round": 1,
        }
    )
    assert objection.requested_tools[0].tool == "get_change_history"
    assert objection.resolved is False


def test_objection_requested_tools_are_validated():
    with pytest.raises(ValidationError):
        Objection.model_validate(
            {
                "objection_id": "O1",
                "hypothesis_id": None,
                "reason": "need more",
                "blocking": False,
                "requested_tools": [{"tool": "run_sql", "args": {}}],
                "round": 1,
            }
        )
