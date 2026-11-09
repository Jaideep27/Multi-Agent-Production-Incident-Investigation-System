"""Aggregating scored runs into the results table (pure functions)."""

import pytest

from investigator.scorer.report import render_markdown, summarise


def row(system, plan_id, correct, outcome_ok, expected="identifiable", confidence=0.8,
        cited=4, existing=4, recall=0.5, decoy=False, as_support=0, tools=10, llm=12,
        tokens_in=1000, tokens_out=100, latency_ms=60000, status="completed"):
    return {
        "system": system, "plan_id": plan_id, "category": "config_change",
        "expected_outcome": expected, "status": status,
        "root_cause_correct": correct, "outcome_correct": outcome_ok,
        "grounding_cited": cited, "grounding_existing": existing, "decisive_recall": recall,
        "decoy_hit": decoy, "decoys_cited_as_support": as_support, "confidence": confidence,
        "tool_call_count": tools, "llm_call_count": llm, "input_tokens": tokens_in,
        "output_tokens": tokens_out, "latency_ms": latency_ms,
    }


ROWS = [
    row("multi_agent", "A-1", True, True, confidence=0.9),
    row("multi_agent", "A-2", False, True, confidence=0.6, decoy=True, existing=3),
    row("multi_agent", "A-3", True, True, expected="inconclusive", confidence=0.3, recall=None),
    row("single_agent", "A-1", True, True, confidence=0.8, tools=6, llm=5),
    row("single_agent", "A-2", False, False, confidence=0.9, as_support=2, tools=6, llm=5),
    row("single_agent", "A-3", False, False, expected="inconclusive", confidence=0.7, recall=None,
        tools=6, llm=5),
]


def test_a_right_top_guess_in_an_inconclusive_report_is_not_a_committed_answer():
    rows = [
        row("multi_agent", "A-1", True, True),
        row("multi_agent", "A-2", True, False, confidence=0.25),
    ]
    multi = summarise(rows)["multi_agent"]
    assert multi["root_cause_accuracy"] == pytest.approx(1.0)
    assert multi["committed_accuracy"] == pytest.approx(0.5)
    assert "| Right and committed to it | 50% |" in render_markdown(rows)


def test_the_final_score_counts_right_answers_out_of_all_runs():
    multi = summarise(ROWS)["multi_agent"]
    single = summarise(ROWS)["single_agent"]
    assert multi["root_cause_right"] == 2
    assert multi["runs"] == 3
    assert single["root_cause_right"] == 1


def test_summary_per_system():
    summary = summarise(ROWS)
    multi = summary["multi_agent"]
    assert multi["runs"] == 3
    assert multi["root_cause_accuracy"] == pytest.approx(2 / 3)
    assert multi["identifiable_accuracy"] == pytest.approx(1 / 2)
    assert multi["inconclusive_handled"] == "1/1"
    assert multi["grounding_rate"] == pytest.approx(11 / 12)
    assert multi["decisive_recall"] == pytest.approx(0.5)
    assert multi["decoy_hit_rate"] == pytest.approx(1 / 3)
    assert multi["mean_tool_calls"] == pytest.approx(10)


def test_calibration_compares_confidence_with_correctness():
    single = summarise(ROWS)["single_agent"]
    # Brier score: mean of (confidence - correct)^2 = (0.04 + 0.81 + 0.49) / 3
    assert single["brier"] == pytest.approx((0.2 ** 2 + 0.9 ** 2 + 0.7 ** 2) / 3)
    assert single["mean_confidence_correct"] == pytest.approx(0.8)
    assert single["mean_confidence_wrong"] == pytest.approx(0.8)
    assert single["decoys_cited_as_support"] == 2


def test_runs_without_hypotheses_are_left_out_of_calibration():
    rows = [row("multi_agent", "A-1", False, False, confidence=None)]
    summary = summarise(rows)["multi_agent"]
    assert summary["brier"] is None
    assert summary["runs"] == 1


def test_collaboration_counts_are_summed_per_system():
    rows = [dict(row("multi_agent", "A-1", True, True), stats={"blocking_objections": 2,
             "critic_requested_tool_calls": 3, "top_hypothesis_changes": 1, "dependent_tasks": 1}),
            dict(row("multi_agent", "A-2", True, True), stats={"blocking_objections": 1})]
    multi = summarise(rows)["multi_agent"]
    assert multi["blocking_objections"] == 3
    assert multi["critic_requested_tool_calls"] == 3
    assert multi["top_hypothesis_changes"] == 1
    assert multi["dependent_tasks"] == 1
    assert "| Blocking Critic objections |" in render_markdown(rows)


def test_p95_latency_attempts_and_total_tokens():
    rows = []
    for index in range(20):
        # latencies 1..20 s; one run made 4 retried attempts on top of its 10 logical calls
        rows.append(dict(row("multi_agent", "B-%d" % index, True, True,
                             latency_ms=(index + 1) * 1000, tokens_in=1000, tokens_out=200),
                         tool_attempts=14 if index == 0 else 10))
    multi = summarise(rows)["multi_agent"]
    # nearest-rank p95 of 1..20 is the 19th value
    assert multi["p95_latency_s"] == pytest.approx(19.0)
    assert multi["mean_tool_attempts"] == pytest.approx((14 + 19 * 10) / 20)
    assert multi["mean_total_tokens"] == pytest.approx(1200)
    text = render_markdown(rows)
    assert "| Latency p95 (s) |" in text
    assert "| Tool attempts incl. retries per run |" in text


def test_attempts_are_left_blank_when_not_recorded():
    multi = summarise([row("multi_agent", "A-1", True, True)])["multi_agent"]
    assert multi["mean_tool_attempts"] is None


def test_markdown_has_a_summary_and_a_row_per_incident():
    text = render_markdown(ROWS)
    assert "| Root-cause accuracy |" in text
    assert "multi_agent" in text and "single_agent" in text
    for plan_id in ["A-1", "A-2", "A-3"]:
        assert "| %s |" % plan_id in text
