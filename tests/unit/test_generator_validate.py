"""The generator's validator: every check is shown to fire on a crafted problem.

Each test starts from the valid PG-1 build and breaks exactly one thing, so a check cannot
pass because something else is wrong.
"""

from datetime import timedelta

import pytest

from investigator.generator.build import build_incident
from investigator.generator.dataset import (
    AlternativeRow,
    DecisiveRow,
    DecoyRow,
    ToolBehaviorRow,
    WithheldRow,
)
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.generator.validate import find_problems


@pytest.fixture(scope="module")
def pg1():
    return build_incident(find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1"))


@pytest.fixture
def dataset(pg1):
    """A private deep copy that the test may break."""
    assert find_problems(pg1) == []
    return pg1.model_copy(deep=True)


def codes(problems):
    found = []
    for problem in problems:
        code = problem.split(":")[0]
        if code not in found:
            found.append(code)
    return found


def first_decisive(dataset, prefix):
    for row in dataset.truth.decisive:
        if row.evidence_id.startswith(prefix):
            return row
    raise AssertionError("no decisive evidence with prefix " + prefix)


def make_inconclusive(dataset):
    scenario = dataset.truth.scenario
    scenario.expected_outcome = "inconclusive"
    scenario.solvable_after_noise = False
    scenario.unidentifiable_reason = "the change record is unreachable"


# ------------------------------------------------------------------ identifiers


def test_duplicate_evidence_id(dataset):
    dataset.evidence.logs[1].evidence_id = dataset.evidence.logs[0].evidence_id
    assert "duplicate_evidence_id" in codes(find_problems(dataset))


def test_wrong_prefix_for_the_kind(dataset):
    log = dataset.evidence.logs[0]
    log.evidence_id = "M" + log.evidence_id[1:]
    assert "wrong_prefix" in codes(find_problems(dataset))


def test_evidence_id_from_another_incident(dataset):
    log = dataset.evidence.logs[0]
    parts = log.evidence_id.split("-")
    log.evidence_id = parts[0] + "-0badf00d-" + parts[2]
    assert "foreign_incident_hash" in codes(find_problems(dataset))


# ------------------------------------------------------------------ references and time


def test_log_from_an_unknown_service(dataset):
    dataset.evidence.logs[0].service = "ghost-service"
    assert "unknown_service" in codes(find_problems(dataset))


def test_dependency_on_an_unknown_service(dataset):
    dataset.evidence.dependencies[0].callee = "ghost-service"
    assert "unknown_service" in codes(find_problems(dataset))


def test_log_outside_the_incident_window(dataset):
    dataset.evidence.logs[0].ts = dataset.evidence.incident.window_start - timedelta(minutes=5)
    assert "outside_window" in codes(find_problems(dataset))


def test_metric_point_outside_the_incident_window(dataset):
    point = dataset.evidence.metrics[0].points[0]
    point.ts = dataset.evidence.incident.window_end + timedelta(minutes=1)
    assert "outside_window" in codes(find_problems(dataset))


def test_truth_points_at_missing_evidence(dataset):
    dataset.truth.decoys.append(
        DecoyRow(evidence_id=dataset.evidence.logs[0].evidence_id[:-6] + "999999",
                 decoy_type="unrelated_spike")
    )
    assert "missing_evidence" in codes(find_problems(dataset))


# ------------------------------------------------------------------ truth invariants
# The codes below are the violation names of truth.invariant_violations (004_truth.sql).


def test_evidence_both_decisive_and_decoy(dataset):
    decisive = first_decisive(dataset, "C-")
    dataset.truth.decoys.append(DecoyRow(evidence_id=decisive.evidence_id,
                                         decoy_type="red_herring_change"))
    assert "evidence_is_both_decisive_and_decoy" in codes(find_problems(dataset))


def test_identifiable_without_retrievable_decisive_evidence(dataset):
    dataset.truth.decisive = []
    assert "identifiable_without_retrievable_decisive_evidence" in codes(find_problems(dataset))


def test_identifiable_with_unreachable_decisive_evidence(dataset):
    first_decisive(dataset, "C-").availability = "unreachable"
    assert "identifiable_with_unreachable_decisive_evidence" in codes(find_problems(dataset))


def test_identifiable_with_withheld_evidence(dataset):
    dataset.truth.withheld.append(WithheldRow(kind="log", description="lock logs",
                                              would_have_shown=None))
    assert "identifiable_with_withheld_evidence" in codes(find_problems(dataset))


def test_inconclusive_with_retrievable_decisive_evidence(dataset):
    make_inconclusive(dataset)
    dataset.truth.alternatives.append(
        AlternativeRow(category="dependency_failure", component="bank-adapter",
                       why_plausible="looks like a bank outage")
    )
    assert "inconclusive_with_retrievable_decisive_evidence" in codes(find_problems(dataset))


def test_inconclusive_without_unreachable_or_withheld_evidence(dataset):
    make_inconclusive(dataset)
    dataset.truth.decisive = []
    dataset.truth.alternatives.append(
        AlternativeRow(category="dependency_failure", component="bank-adapter",
                       why_plausible="looks like a bank outage")
    )
    found = codes(find_problems(dataset))
    assert "inconclusive_without_unreachable_or_withheld_evidence" in found


def test_inconclusive_without_alternative_explanation(dataset):
    make_inconclusive(dataset)
    for row in dataset.truth.decisive:
        row.availability = "unreachable"
    assert "inconclusive_without_alternative_explanation" in codes(find_problems(dataset))


def block_every_decisive_tool(dataset):
    dataset.evidence.tool_behavior = [
        ToolBehaviorRow(tool_name="get_change_history", rules={"mode": "persistent"}),
        ToolBehaviorRow(tool_name="query_logs", rules={"mode": "persistent"}),
        ToolBehaviorRow(tool_name="query_metrics", rules={"mode": "persistent"}),
    ]


def test_a_well_formed_inconclusive_variant_is_accepted(dataset):
    # Guards the inconclusive tests above: with every rule met, none of them fires.
    make_inconclusive(dataset)
    for row in dataset.truth.decisive:
        row.availability = "unreachable"
    block_every_decisive_tool(dataset)
    dataset.truth.alternatives.append(
        AlternativeRow(category="dependency_failure", component="bank-adapter",
                       why_plausible="looks like a bank outage")
    )
    assert find_problems(dataset) == []


def test_unreachable_evidence_needs_a_persistent_rule_on_its_tool(dataset):
    make_inconclusive(dataset)
    for row in dataset.truth.decisive:
        row.availability = "unreachable"
    block_every_decisive_tool(dataset)
    dataset.evidence.tool_behavior = dataset.evidence.tool_behavior[1:]   # changes not blocked
    dataset.truth.alternatives.append(
        AlternativeRow(category="dependency_failure", component="bank-adapter",
                       why_plausible="looks like a bank outage")
    )
    found = find_problems(dataset)
    assert codes(found) == ["unreachable_without_persistent_rule"]
    assert "C-" in found[0]


# ------------------------------------------------------------------ solvability


def test_identifiable_incident_with_a_persistent_tool_failure(dataset):
    dataset.evidence.tool_behavior.append(
        ToolBehaviorRow(tool_name="get_change_history", rules={"mode": "persistent"})
    )
    assert "blocking_failure_on_identifiable" in codes(find_problems(dataset))


def test_identifiable_incident_may_lose_a_tool_no_decisive_evidence_needs(dataset):
    # No decisive evidence comes from past incidents, so that tool may be down for good.
    dataset.evidence.tool_behavior.append(
        ToolBehaviorRow(tool_name="search_past_incidents", rules={"mode": "persistent", "status": "error"})
    )
    assert "blocking_failure_on_identifiable" not in codes(find_problems(dataset))


def test_identifiable_incident_with_a_probabilistic_tool_failure(dataset):
    dataset.evidence.tool_behavior.append(
        ToolBehaviorRow(tool_name="query_metrics", rules={"mode": "probabilistic", "p": 0.5})
    )
    assert "blocking_failure_on_identifiable" in codes(find_problems(dataset))


def test_tool_rule_with_an_unknown_mode(dataset):
    dataset.evidence.tool_behavior[0].rules = {"mode": "sometimes"}
    assert "unknown_failure_mode" in codes(find_problems(dataset))


def test_tool_rule_the_server_would_reject(dataset):
    # Same parser as the MCP server, so a rule that loads is a rule the server can run.
    dataset.evidence.tool_behavior[0].rules = {"mode": "transient", "fail_first_n": 0}
    assert "invalid_failure_rule" in codes(find_problems(dataset))


def test_tool_rule_matching_an_argument_the_tool_lacks(dataset):
    dataset.evidence.tool_behavior[0].rules = {"mode": "persistent", "when": {"include_diff": True}}
    assert "invalid_failure_rule" in codes(find_problems(dataset))


def test_decisive_log_must_not_sit_inside_a_log_gap(dataset):
    decisive = first_decisive(dataset, "L-")
    log = None
    for item in dataset.evidence.logs:
        if item.evidence_id == decisive.evidence_id:
            log = item
    for row in dataset.truth.noise:
        if row.noise_type == "log_gap":
            row.params["service"] = log.service
            row.params["start"] = (log.ts - timedelta(minutes=1)).isoformat()
            row.params["end"] = (log.ts + timedelta(minutes=1)).isoformat()
    assert "log_inside_gap" in codes(find_problems(dataset))


# ------------------------------------------------------------------ leakage


def test_plan_id_leaked_into_a_log_message(dataset):
    dataset.evidence.logs[0].message = "replaying PG-1 traffic"
    assert "answer_key_leak" in codes(find_problems(dataset))


def test_archetype_name_leaked_into_a_change_diff(dataset):
    archetype = dataset.truth.scenario.archetype
    dataset.evidence.changes[0].diff = {"note": "simulates " + archetype}
    assert "answer_key_leak" in codes(find_problems(dataset))


def test_mechanism_leaked_into_the_incident_summary(dataset):
    dataset.evidence.incident.summary = dataset.truth.scenario.mechanism
    assert "answer_key_leak" in codes(find_problems(dataset))


def test_leak_check_looks_inside_nested_values(dataset):
    dataset.evidence.past_incidents[0].symptoms.append("decoy")
    assert "answer_key_leak" in codes(find_problems(dataset))


def test_decisive_row_types_are_checked(dataset):
    # Not a validator code, a model rule: availability is one of two words.
    with pytest.raises(ValueError):
        DecisiveRow(evidence_id=dataset.truth.decisive[0].evidence_id, availability="maybe")
