"""Orchestrator plans may chain tasks: `after` points at an earlier task of the same plan."""

import pytest
from pydantic import ValidationError

from investigator.agents.outputs import OrchestratorOutput


def plan(*tasks):
    return {"tasks": list(tasks)}


def test_a_task_may_wait_for_an_earlier_task():
    output = OrchestratorOutput.model_validate(plan(
        {"agent": "observability", "goal": "find the first failing service"},
        {"agent": "dependency", "goal": "check changes on that service", "after": 1},
    ))
    assert output.tasks[1].after == 1


def test_after_cannot_point_forward_or_at_itself():
    for after in [2, 3, 0]:
        with pytest.raises(ValidationError):
            OrchestratorOutput.model_validate(plan(
                {"agent": "observability", "goal": "a"},
                {"agent": "dependency", "goal": "b", "after": after},
            ))


def test_tasks_without_after_run_independently():
    output = OrchestratorOutput.model_validate(plan({"agent": "observability", "goal": "a"}))
    assert output.tasks[0].after is None
