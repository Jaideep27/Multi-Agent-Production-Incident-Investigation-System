"""The routing rule after each critique (pure, so every branch is tested directly)."""

from investigator.agents.team import next_step


def test_no_blocking_objection_finishes_the_run():
    assert next_step(1, 3, open_blocking=False, stop_reason=None) == ("report", "completed")


def test_a_blocking_objection_starts_another_round():
    assert next_step(1, 3, open_blocking=True, stop_reason=None) == ("plan", "running")


def test_the_last_round_reports_even_with_objections():
    assert next_step(3, 3, open_blocking=True, stop_reason=None) == ("report", "max_rounds")


def test_a_spent_budget_reports_at_once():
    assert next_step(1, 3, open_blocking=True, stop_reason="tool_budget") == ("report", "budget_exhausted")
    assert next_step(1, 3, open_blocking=False, stop_reason="llm_budget") == ("report", "budget_exhausted")
