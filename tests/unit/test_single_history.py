"""The single agent keeps a step-by-step history too, shown on the same panel as the team's."""

import asyncio
from types import SimpleNamespace

from investigator.agents.baseline import BaselineContext, run_baseline
from investigator.agents.conversation import Conversation
from investigator.blackboard.store import Blackboard
from investigator.llm.client import LlmOutputError
from investigator.web.data import DataStore


class FakeGateway:
    def __init__(self):
        self.requests = []

    def calls_made(self):
        return []

    def seen_evidence_ids(self):
        return []

    async def execute(self, requests):
        self.requests.append(requests)
        outcomes = []
        for request in requests:
            outcomes.append(SimpleNamespace(request=request, result=None, source="executed"))
        return outcomes


class ScriptedLlm:
    def __init__(self, answers):
        self.answers = answers

    async def structured(self, agent, system, prompt, output_model, check=None):
        if not self.answers:
            raise LlmOutputError("no scripted answer", attempts=1)
        return output_model.model_validate(self.answers.pop(0))


BRIEF = {"services": [["api", "app"]], "reported_at": "t0", "reporter": "oncall",
         "summary": "errors", "window_start": "t-1", "window_end": "t1"}


def run(tmp_path, answers, with_history=True):
    board = Blackboard.create(tmp_path / "history.sqlite")
    history = None
    if with_history:
        history = Conversation(board)
    ctx = BaselineContext(brief=BRIEF, llm=ScriptedLlm(answers), gateway=FakeGateway(), max_steps=3,
                          history=history)
    asyncio.run(run_baseline(ctx))
    return ctx, board


def step_with_a_note_and_a_tool():
    return {"notes": [{"kind": "assumption", "statement": "the release looks suspicious"}],
            "tool_requests": [{"tool": "get_service_dependencies", "args": {}}]}


def test_each_step_of_the_single_agent_is_recorded_in_order(tmp_path):
    _, board = run(tmp_path, [step_with_a_note_and_a_tool()])
    entries = board.history()
    kinds = []
    for entry in entries:
        kinds.append(entry["intent"])
    assert kinds == ["facts", "tools", "report"]
    assert entries[0]["sender"] == "investigator"
    assert entries[0]["recipient"] == "notes"
    assert "the release looks suspicious" in entries[0]["text"]
    assert "get_service_dependencies" in entries[1]["text"]
    assert entries[0]["round"] == 1


def test_later_steps_carry_their_step_number(tmp_path):
    _, board = run(tmp_path, [step_with_a_note_and_a_tool(), step_with_a_note_and_a_tool()])
    rounds = []
    for entry in board.history():
        if entry["intent"] == "tools":
            rounds.append(entry["round"])
    assert rounds == [1, 2]


def test_the_single_agent_runs_without_a_history_too(tmp_path):
    ctx, board = run(tmp_path, [step_with_a_note_and_a_tool()], with_history=False)
    assert ctx.report is not None
    assert board.history() == []


def test_the_viewer_finds_a_single_agent_history(tmp_path):
    run_dir = tmp_path / "11111111-2222-3333-4444-555555555555"
    run_dir.mkdir()
    board = Blackboard.create(run_dir / "history.sqlite")
    board.add_message("investigator", "notes", "facts", "a note", [], 1)
    board.close()
    store = DataStore("label", runs_dir=tmp_path, conninfo="unused")
    found = store.conversation("11111111-2222-3333-4444-555555555555")
    assert found["history"][0]["text"] == "a note"


def test_the_viewer_still_finds_a_team_conversation(tmp_path):
    run_dir = tmp_path / "11111111-2222-3333-4444-555555555555"
    run_dir.mkdir()
    board = Blackboard.create(run_dir / "blackboard.sqlite")
    board.add_message("critic", "reasoning", "challenge", "why?", [], 1)
    board.close()
    store = DataStore("label", runs_dir=tmp_path, conninfo="unused")
    assert store.conversation("11111111-2222-3333-4444-555555555555")["history"][0]["text"] == "why?"
