"""Step 8 gate: scorer golden tests. Hand-written reports for PG-1, each with its exact score.

The truth comes from the real PG-1 build (the scorer is the one component allowed to read
it). Evidence IDs are taken from that build, so the tests do not depend on numbering.
"""

import pytest

from investigator.generator.build import build_incident
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.models.report import FinalReport
from investigator.scorer.metrics import score_report
from investigator.scorer.truth import truth_from_dataset


@pytest.fixture(scope="module")
def pg1():
    return build_incident(find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1"))


@pytest.fixture(scope="module")
def truth(pg1):
    return truth_from_dataset(pg1)


def decisive(pg1):
    ids = []
    for row in pg1.truth.decisive:
        ids.append(row.evidence_id)
    return ids


def decoy_id(pg1, decoy_type):
    for row in pg1.truth.decoys:
        if row.decoy_type == decoy_type:
            return row.evidence_id
    raise AssertionError(decoy_type)


def all_ids(pg1):
    ids = []
    evidence = pg1.evidence
    for group in [evidence.dependencies, evidence.logs, evidence.metrics, evidence.changes,
                  evidence.past_incidents]:
        for item in group:
            ids.append(item.evidence_id)
    return ids


def report(outcome, hypotheses, cited, actions=True, unresolved=None):
    """hypotheses: [component, category, confidence, supporting ids]"""
    rows = []
    rank = 1
    for component, category, confidence, supporting in hypotheses:
        rows.append({"rank": rank, "component": component, "category": category,
                     "mechanism": "m", "confidence": confidence,
                     "supporting_evidence_ids": supporting})
        rank = rank + 1
    data = {"outcome": outcome, "summary": "s", "hypotheses": rows,
            "facts": [{"statement": "f", "evidence_ids": cited}] if cited else []}
    if actions and outcome == "root_cause_identified":
        data["recommended_actions"] = [{"action": "a", "priority": "immediate", "rationale": "r"}]
    if unresolved is not None:
        data["unresolved"] = unresolved
    return FinalReport.model_validate(data)


def test_truth_view_holds_the_answer_key(pg1, truth):
    assert truth.expected_outcome == "identifiable"
    assert truth.root_component == "payment-router"
    assert truth.root_category == "config_change"
    assert sorted(truth.decisive_ids) == sorted(decisive(pg1))
    targets = {}
    for target in truth.decoys:
        targets[target.decoy_type] = [target.component, target.category]
    assert targets == {"red_herring_change": ["notification-service", None],
                       "lookalike_history": ["bank-adapter", "dependency_failure"]}


def test_golden_correct_and_fully_grounded(pg1, truth):
    ids = decisive(pg1)
    score = score_report(report("root_cause_identified",
                                [["payment-router", "config_change", 0.85, ids]], ids),
                         truth, seen_ids=all_ids(pg1))
    assert score.model_dump() == {
        "root_cause_correct": True, "outcome_correct": True,
        "grounding_cited": 3, "grounding_existing": 3, "decisive_recall": 1.0,
        "decoy_hit": False, "decoys_cited_as_support": 0, "confidence": 0.85,
    }


def test_golden_component_names_are_compared_loosely(pg1, truth):
    score = score_report(report("root_cause_identified",
                                [[" Payment-Router ", "config_change", 0.7, []]], []),
                         truth, seen_ids=[])
    assert score.root_cause_correct is True


def test_golden_red_herring_deploy_blamed(pg1, truth):
    decoy = decoy_id(pg1, "red_herring_change")
    score = score_report(report("root_cause_identified",
                                [["notification-service", "bad_deploy", 0.8, [decoy]]], [decoy]),
                         truth, seen_ids=all_ids(pg1))
    assert score.model_dump() == {
        "root_cause_correct": False, "outcome_correct": True,
        "grounding_cited": 1, "grounding_existing": 1, "decisive_recall": 0.0,
        "decoy_hit": True, "decoys_cited_as_support": 1, "confidence": 0.8,
    }


def test_golden_lookalike_history_followed(pg1, truth):
    decoy = decoy_id(pg1, "lookalike_history")
    score = score_report(report("root_cause_identified",
                                [["bank-adapter", "dependency_failure", 0.6, [decoy]]], [decoy]),
                         truth, seen_ids=all_ids(pg1))
    assert score.decoy_hit is True
    assert score.root_cause_correct is False


def test_golden_same_component_as_a_lookalike_but_other_category_is_no_decoy_hit(pg1, truth):
    score = score_report(report("root_cause_identified",
                                [["bank-adapter", "cert_expiry", 0.6, []]], []),
                         truth, seen_ids=[])
    assert score.decoy_hit is False


def test_golden_wrong_answer_that_is_not_a_decoy(pg1, truth):
    score = score_report(report("root_cause_identified",
                                [["postgres-payments", "db_contention", 0.5, []]], []),
                         truth, seen_ids=[])
    assert score.root_cause_correct is False
    assert score.decoy_hit is False


def test_golden_right_cause_but_decoy_cited_as_support(pg1, truth):
    ids = decisive(pg1)
    decoy = decoy_id(pg1, "red_herring_change")
    score = score_report(report("root_cause_identified",
                                [["payment-router", "config_change", 0.85, ids + [decoy]]],
                                ids + [decoy]),
                         truth, seen_ids=all_ids(pg1))
    assert score.root_cause_correct is True
    assert score.decoy_hit is False          # the top hypothesis is right
    assert score.decoys_cited_as_support == 1


def test_golden_identifiable_incident_reported_inconclusive(pg1, truth):
    ids = decisive(pg1)[:1]
    score = score_report(report("inconclusive",
                                [["payment-router", "config_change", 0.4, ids]], ids,
                                unresolved=["diff not seen"]),
                         truth, seen_ids=all_ids(pg1))
    assert score.root_cause_correct is True      # the ranking was right ...
    assert score.outcome_correct is False        # ... but it did not commit
    assert score.decisive_recall == pytest.approx(1 / 3)


def test_golden_citations_that_never_came_back_from_a_tool(pg1, truth):
    ids = decisive(pg1)
    invented = "L-3d0e8a6c-999999"
    score = score_report(report("root_cause_identified",
                                [["payment-router", "config_change", 0.9, ids]], ids + [invented]),
                         truth, seen_ids=ids[:2])
    assert score.grounding_cited == 4
    assert score.grounding_existing == 2


def test_golden_no_hypotheses_at_all(pg1, truth):
    score = score_report(report("inconclusive", [], [], unresolved=["nothing found"]),
                         truth, seen_ids=[])
    assert score.model_dump() == {
        "root_cause_correct": False, "outcome_correct": False,
        "grounding_cited": 0, "grounding_existing": 0, "decisive_recall": 0.0,
        "decoy_hit": False, "decoys_cited_as_support": 0, "confidence": None,
    }


def test_golden_inconclusive_incident_is_correct_only_when_reported_inconclusive(pg1, truth):
    inconclusive_truth = truth.model_copy(update={"expected_outcome": "inconclusive"})
    right_call = score_report(report("inconclusive", [["payment-router", "config_change", 0.3, []]],
                                     [], unresolved=["decisive change unreachable"]),
                              inconclusive_truth, seen_ids=[])
    overconfident = score_report(report("root_cause_identified",
                                        [["payment-router", "config_change", 0.9, []]], []),
                                 inconclusive_truth, seen_ids=[])
    assert right_call.root_cause_correct is True
    assert right_call.outcome_correct is True
    # even naming the true cause is wrong when the evidence could not support it
    assert overconfident.root_cause_correct is False
    assert overconfident.outcome_correct is False
