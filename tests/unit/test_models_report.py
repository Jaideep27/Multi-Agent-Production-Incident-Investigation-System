"""Tests for the final report and score models (investigator.models.report,
investigator.models.scores)."""

import uuid

import pytest
from pydantic import ValidationError

from investigator.models.report import FinalReport
from investigator.models.scores import ScoreRecord


def make_hypothesis(rank, confidence, component="payment-router"):
    return {
        "rank": rank,
        "component": component,
        "category": "config_change",
        "mechanism": "pool shrunk from 100 to 20",
        "confidence": confidence,
        "supporting_evidence_ids": ["C-aaaaaaaa-1", "L-aaaaaaaa-2"],
        "contradicting_evidence_ids": [],
        "assumptions": [],
    }


def make_report(**fields):
    base = {
        "outcome": "root_cause_identified",
        "summary": "A config change shrank the DB pool.",
        "hypotheses": [make_hypothesis(1, 0.8), make_hypothesis(2, 0.15, "notification")],
        "facts": [{"statement": "pool size changed 100 -> 20", "evidence_ids": ["C-aaaaaaaa-1"]}],
        "assumptions": ["traffic was normal for a weekday"],
        "recommended_actions": [
            {
                "action": "restore pool size to 100",
                "priority": "immediate",
                "rationale": "the drop matches the change time",
                "evidence_ids": ["C-aaaaaaaa-1"],
            }
        ],
        "unresolved": [],
    }
    for key in fields:
        base[key] = fields[key]
    return FinalReport.model_validate(base)


# -------------------------------------------------------------------------- report


def test_identified_report_is_valid():
    report = make_report()
    assert report.hypotheses[0].rank == 1


def test_report_survives_a_json_round_trip():
    report = make_report()
    again = FinalReport.model_validate_json(report.model_dump_json())
    assert again == report


def test_identified_report_needs_a_hypothesis():
    with pytest.raises(ValidationError):
        make_report(hypotheses=[])


def test_identified_report_needs_a_recommended_action():
    with pytest.raises(ValidationError):
        make_report(recommended_actions=[])


def test_ranks_must_run_from_one_without_gaps():
    with pytest.raises(ValidationError):
        make_report(hypotheses=[make_hypothesis(1, 0.8), make_hypothesis(3, 0.1, "x")])
    with pytest.raises(ValidationError):
        make_report(hypotheses=[make_hypothesis(2, 0.8), make_hypothesis(1, 0.1, "x")])


def test_confidence_must_not_rise_with_rank():
    with pytest.raises(ValidationError):
        make_report(hypotheses=[make_hypothesis(1, 0.3), make_hypothesis(2, 0.6, "x")])


def test_inconclusive_report_must_say_what_is_unresolved():
    with pytest.raises(ValidationError):
        make_report(outcome="inconclusive", unresolved=[])


def test_inconclusive_report_may_still_list_candidates_and_no_actions():
    report = make_report(
        outcome="inconclusive",
        hypotheses=[make_hypothesis(1, 0.35), make_hypothesis(2, 0.3, "x")],
        recommended_actions=[],
        unresolved=["lock wait logs could not be retrieved"],
    )
    assert report.outcome == "inconclusive"
    assert len(report.hypotheses) == 2


def test_report_facts_must_cite_evidence():
    with pytest.raises(ValidationError):
        make_report(facts=[{"statement": "the pool shrank", "evidence_ids": []}])


def test_evidence_cannot_both_support_and_contradict_one_hypothesis():
    hypothesis = make_hypothesis(1, 0.8)
    hypothesis["contradicting_evidence_ids"] = ["C-aaaaaaaa-1"]
    with pytest.raises(ValidationError):
        make_report(hypotheses=[hypothesis])


def test_report_rejects_unknown_outcome():
    with pytest.raises(ValidationError):
        make_report(outcome="probably")


def test_report_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        make_report(plan_id="PG-1")


# -------------------------------------------------------------------------- scores


def make_score(**fields):
    base = {
        "run_id": str(uuid.uuid4()),
        "root_cause_correct": True,
        "outcome_correct": True,
        "grounding_cited": 5,
        "grounding_existing": 5,
        "grounding_supporting": 4,
        "decisive_recall": 0.5,
        "decoy_hit": False,
        "confidence": 0.8,
        "tool_call_count": 12,
        "llm_call_count": 9,
        "input_tokens": 40000,
        "output_tokens": 3000,
        "latency_ms": 95000,
        "rounds": 2,
    }
    for key in fields:
        base[key] = fields[key]
    return ScoreRecord.model_validate(base)


def test_score_is_valid():
    assert make_score().grounding_existing == 5


def test_existing_citations_cannot_exceed_cited():
    # Mirrors the CHECK constraint on eval.scores.
    with pytest.raises(ValidationError):
        make_score(grounding_cited=3, grounding_existing=4)


@pytest.mark.parametrize("field", ["decisive_recall", "confidence"])
def test_score_probabilities_are_bounded(field):
    with pytest.raises(ValidationError):
        make_score(**{field: 1.5})


def test_score_counts_cannot_be_negative():
    with pytest.raises(ValidationError):
        make_score(tool_call_count=-1)
