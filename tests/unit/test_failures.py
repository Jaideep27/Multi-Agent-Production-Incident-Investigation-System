"""Failure injection: rule parsing and deterministic decisions.

Determinism contract: a decision depends only on (seed, tool, normalised args, attempt), so
the same calls give the same failures in any order and in any process.
"""

import random

import pytest
from pydantic import ValidationError

from investigator.models.tools import ToolRequest
from investigator.tools.failures import FailureInjector, FailureRule, parse_rules

SEED = "inc_0000000a"


def args_for(tool, raw):
    return ToolRequest(tool=tool, args=raw).args


def injector(tool, rules):
    return FailureInjector(seed=SEED, rules=[[tool, rules]])


# ------------------------------------------------------------------ rule parsing


def test_rule_defaults():
    rule = FailureRule.model_validate({"mode": "transient"})
    assert rule.status == "timeout"
    assert rule.fail_first_n == 1
    assert rule.when == {}


def test_rule_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        FailureRule.model_validate({"mode": "sometimes"})


def test_rule_rejects_unknown_field():
    with pytest.raises(ValidationError):
        FailureRule.model_validate({"mode": "transient", "surprise": 1})


def test_rule_rejects_probability_out_of_range():
    with pytest.raises(ValidationError):
        FailureRule.model_validate({"mode": "probabilistic", "p": 1.5})


def test_rule_rejects_a_when_key_the_tool_does_not_have():
    with pytest.raises(ValueError, match="no_such_arg"):
        parse_rules([["query_logs", {"mode": "persistent", "when": {"no_such_arg": 1}}]])


def test_parse_rules_rejects_two_rules_for_one_tool():
    with pytest.raises(ValueError, match="query_logs"):
        parse_rules([["query_logs", {"mode": "persistent"}], ["query_logs", {"mode": "partial"}]])


def test_the_seed_data_rules_parse():
    # tests/db_seed.py uses these shapes; the server must accept them.
    parse_rules([
        ["query_logs", {"mode": "transient", "fail_first_n": 1}],
        ["get_change_history", {"mode": "partial", "keep_fraction": 0.5}],
    ])


# ------------------------------------------------------------------ decisions


def test_no_rule_means_pass():
    decision = FailureInjector(seed=SEED, rules=[]).decide("query_logs", {}, 1)
    assert decision.action == "pass"


def test_rule_for_another_tool_does_not_apply():
    decision = injector("query_metrics", {"mode": "persistent"}).decide(
        "query_logs", args_for("query_logs", {}), 1)
    assert decision.action == "pass"


def test_transient_fails_the_first_n_attempts_then_passes():
    chosen = injector("query_logs", {"mode": "transient", "fail_first_n": 2, "status": "error"})
    args = args_for("query_logs", {"service": "api"})
    actions = []
    for attempt in [1, 2, 3]:
        decision = chosen.decide("query_logs", args, attempt)
        actions.append([decision.action, decision.status])
    assert actions == [["fail", "error"], ["fail", "error"], ["pass", None]]


def test_persistent_fails_every_attempt():
    chosen = injector("get_change_history", {"mode": "persistent"})
    args = args_for("get_change_history", {})
    for attempt in range(1, 6):
        decision = chosen.decide("get_change_history", args, attempt)
        assert decision.action == "fail"
        assert decision.status == "timeout"


def test_when_limits_a_rule_to_matching_calls():
    rule = {"mode": "persistent", "when": {"include_diff": True}}
    chosen = injector("get_change_history", rule)
    with_diff = args_for("get_change_history", {"include_diff": True})
    without_diff = args_for("get_change_history", {})
    assert chosen.decide("get_change_history", with_diff, 1).action == "fail"
    assert chosen.decide("get_change_history", without_diff, 1).action == "pass"


def test_partial_returns_a_keep_fraction():
    decision = injector("query_logs", {"mode": "partial", "keep_fraction": 0.25}).decide(
        "query_logs", args_for("query_logs", {}), 1)
    assert decision.action == "partial"
    assert decision.keep_fraction == 0.25


def test_partial_filter_is_deterministic_and_roughly_the_fraction():
    chosen = injector("query_logs", {"mode": "partial", "keep_fraction": 0.5})
    args = args_for("query_logs", {})
    ids = []
    for number in range(1, 401):
        ids.append("L-0000000a-%d" % number)
    first = chosen.keep_ids("query_logs", args, ids, 0.5)
    second = chosen.keep_ids("query_logs", args, list(reversed(ids)), 0.5)
    assert sorted(first) == sorted(second)
    assert 150 < len(first) < 250


def test_partial_filter_depends_on_the_query():
    # An item hidden from one query can still show up in a different one.
    chosen = injector("query_logs", {"mode": "partial", "keep_fraction": 0.5})
    ids = []
    for number in range(1, 101):
        ids.append("L-0000000a-%d" % number)
    first = chosen.keep_ids("query_logs", args_for("query_logs", {"service": "a"}), ids, 0.5)
    second = chosen.keep_ids("query_logs", args_for("query_logs", {"service": "b"}), ids, 0.5)
    assert first != second


def test_probabilistic_fails_about_p_of_distinct_calls():
    chosen = injector("query_logs", {"mode": "probabilistic", "p": 0.3})
    failures = 0
    for number in range(1000):
        args = args_for("query_logs", {"contains": "word%d" % number})
        if chosen.decide("query_logs", args, 1).action == "fail":
            failures = failures + 1
    assert 240 < failures < 360


def test_probabilistic_decision_depends_on_the_attempt():
    # Otherwise a retry could never succeed.
    chosen = injector("query_logs", {"mode": "probabilistic", "p": 0.5})
    differs = False
    for number in range(50):
        args = args_for("query_logs", {"contains": "w%d" % number})
        if chosen.decide("query_logs", args, 1).action != chosen.decide("query_logs", args, 2).action:
            differs = True
    assert differs


def test_decisions_do_not_depend_on_call_order():
    chosen = injector("query_logs", {"mode": "probabilistic", "p": 0.5})
    calls = []
    for number in range(60):
        for attempt in [1, 2, 3]:
            calls.append([args_for("query_logs", {"contains": "x%d" % number}), attempt])

    def outcomes(order):
        found = {}
        for args, attempt in order:
            key = "%s/%d" % (args["contains"], attempt)
            found[key] = chosen.decide("query_logs", args, attempt).action
        return found

    shuffled = list(calls)
    random.Random(7).shuffle(shuffled)
    assert outcomes(calls) == outcomes(shuffled)


def test_a_different_seed_changes_probabilistic_outcomes():
    rules = [["query_logs", {"mode": "probabilistic", "p": 0.5}]]
    first = FailureInjector(seed="inc_0000000a", rules=rules)
    second = FailureInjector(seed="inc_0000000b", rules=rules)
    differs = False
    for number in range(50):
        args = args_for("query_logs", {"contains": "w%d" % number})
        if first.decide("query_logs", args, 1).action != second.decide("query_logs", args, 1).action:
            differs = True
    assert differs
