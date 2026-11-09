"""The Critic's verdict loop: approve, or block once with a follow-up routed to a specialist.

Rules (enforced in Python, not left to the model):
* approved=True ends the investigation, even if older objections are still open;
* a blocking objection must name the competing explanation and the specialist who should
  settle it; Python turns it into a task for that specialist in the next round;
* each hypothesis can be blocked only once per run; a repeat is stored as non-blocking;
* the specialists cannot spend the last tool calls of the budget (kept for follow-ups).
"""

import asyncio

import pytest
from pydantic import ValidationError

from investigator.agents.outputs import CriticOutput, ReasoningOutput
from investigator.agents.team import RunContext, affordable, critique, next_step
from investigator.blackboard.store import Blackboard
from investigator.models.blackboard import Hypothesis


def blocking(hypothesis_id="H-0001", agent="observability"):
    return {"hypothesis_id": hypothesis_id, "reason": "the deploy may come after the errors",
            "blocking": True, "alternative": "the deploy is a coincidence",
            "follow_up_agent": agent,
            "requested_tools": [{"tool": "query_logs", "args": {"levels": ["ERROR"]}}]}


class FixedLlm:
    """Returns one prepared Critic answer, after running the agent's own check on it."""

    def __init__(self, answer):
        self.answer = answer

    async def structured(self, agent, system, prompt, output_model, check=None):
        output = output_model.model_validate(self.answer)
        if check is not None:
            assert check(output) == []
        return output


def make_ctx(tmp_path, answer):
    board = Blackboard.create(tmp_path / "board.sqlite")
    board.save_hypothesis(Hypothesis(hypothesis_id="H-0001", component="api", category="bad_deploy",
                                     mechanism="the release broke it", confidence=0.6,
                                     author="reasoning", round=1))
    brief = {"services": [["api", "app"]], "reported_at": "t0", "reporter": "oncall",
             "summary": "errors", "window_start": "t-1", "window_end": "t1"}
    ctx = RunContext(brief=brief, llm=FixedLlm(answer), gateway=None,
                     board=board, max_rounds=3, max_specialist_steps=3)
    ctx.ranking = ["H-0001"]
    return ctx


# ------------------------------------------------------------------ the verdict schema


def test_approval_cannot_come_with_a_blocking_objection():
    with pytest.raises(ValidationError):
        CriticOutput.model_validate({"approved": True, "objections": [blocking()]})
    CriticOutput.model_validate({"approved": True, "objections": []})


def test_a_blocking_objection_must_name_the_alternative_and_who_settles_it():
    for missing in ["alternative", "follow_up_agent"]:
        objection = blocking()
        del objection[missing]
        with pytest.raises(ValidationError):
            CriticOutput.model_validate({"approved": False, "objections": [objection]})


def test_reasoning_gives_two_to_four_competing_hypotheses():
    one = {"component": "api", "category": "bad_deploy", "mechanism": "m", "confidence": 0.5}
    with pytest.raises(ValidationError):
        ReasoningOutput.model_validate({"hypotheses": [one]})
    five = []
    for category in ["bad_deploy", "config_change", "cache_failure", "db_contention", "cert_expiry"]:
        five.append(dict(one, category=category, confidence=0.1))
    with pytest.raises(ValidationError):
        ReasoningOutput.model_validate({"hypotheses": five})
    ReasoningOutput.model_validate({"hypotheses": five[:2]})


# ------------------------------------------------------------------ routing


def test_approval_ends_the_run_even_with_old_open_objections():
    assert next_step(1, 3, open_blocking=True, stop_reason=None, approved=True) == ("report", "completed")
    assert next_step(1, 3, open_blocking=True, stop_reason=None, approved=False) == ("plan", "running")


def test_a_blocking_objection_becomes_a_follow_up_task_for_the_named_specialist(tmp_path):
    ctx = make_ctx(tmp_path, {"approved": False, "objections": [blocking(agent="dependency")]})
    asyncio.run(critique(ctx, 1))
    tasks = ctx.board.tasks(status="pending")
    assert len(tasks) == 1
    assert tasks[0].agent == "dependency"
    assert tasks[0].round == 2
    assert "O-0001" in tasks[0].goal and "coincidence" in tasks[0].goal
    assert ctx.stats["follow_up_tasks"] == 1
    assert ctx.approved is False


def test_a_hypothesis_can_be_blocked_only_once(tmp_path):
    ctx = make_ctx(tmp_path, {"approved": False, "objections": [blocking()]})
    asyncio.run(critique(ctx, 1))
    asyncio.run(critique(ctx, 2))
    objections = ctx.board.objections()
    assert [o.blocking for o in objections] == [True, False]
    assert ctx.stats["blocking_downgraded"] == 1
    # only the first objection produced a follow-up task
    assert len(ctx.board.tasks()) == 1


def test_approval_is_recorded(tmp_path):
    ctx = make_ctx(tmp_path, {"approved": True, "objections": []})
    asyncio.run(critique(ctx, 1))
    assert ctx.approved is True
    assert ctx.stats["critic_approvals"] == 1


# ------------------------------------------------------------------ the tool-call reserve


def test_specialists_cannot_spend_the_reserved_calls():
    requests = ["a", "b", "c", "d"]
    assert affordable(requests, max_calls=40, made=20, reserve=8) == requests
    assert affordable(requests, max_calls=40, made=30, reserve=8) == ["a", "b"]
    assert affordable(requests, max_calls=40, made=32, reserve=8) == []
    assert affordable(requests, max_calls=40, made=35, reserve=8) == []
