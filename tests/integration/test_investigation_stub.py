"""Step 7 gate (part 1): full investigations of PG-1 on the stub LLM.

Real chain except the model: runner -> LangGraph -> agents -> gateway -> MCP stdio server ->
Postgres, with eval.* logging and a blackboard file per run.
"""

import pytest

from investigator.blackboard.store import Blackboard
from investigator.gateway.gateway import GatewayLimits
from investigator.llm.client import LlmLimits
from investigator.llm.stub import StubBackend
from investigator.runner import RunConfig, run_investigation
from tests.stub_brain import StubBrain

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def remove_test_runs(pg1, admin_conn):
    yield
    admin_conn.execute("DELETE FROM eval.runs WHERE incident_id = %s",
                       (pg1.evidence.incident.incident_id,))


def config_for(tmp_path, **changes):
    settings = {"runs_dir": str(tmp_path / "runs"), "cache_mode": "off",
                "gateway": GatewayLimits(retry_delay_s=0.0)}
    for key in changes:
        settings[key] = changes[key]
    return RunConfig(**settings)


def run_row(admin_conn, run_id):
    return admin_conn.execute(
        "SELECT status, rounds, final_report, ended_at FROM eval.runs WHERE run_id = %s",
        (run_id,),
    ).fetchone()


def board_for(tmp_path, run_id):
    return Blackboard.open(tmp_path / "runs" / run_id / "blackboard.sqlite")


async def test_stub_run_completes_after_the_critic_is_satisfied(pg1, tmp_path, admin_conn):
    brain = StubBrain()
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(brain))

    # Round 1: the critic objects and asks for diffs; round 2: it is satisfied.
    assert result.status == "completed"
    assert result.rounds == 2
    assert result.stats["blocking_objections"] == 1
    assert result.stats["critic_requested_tool_calls"] == 1
    assert result.report.outcome == "root_cause_identified"
    for agent in ["orchestrator", "observability", "dependency", "reasoning", "critic", "reporting"]:
        assert brain.calls_by_agent[agent] >= 1, agent

    status, rounds, final_report, ended_at = run_row(admin_conn, result.run_id)
    assert status == "completed"
    assert rounds == 2
    assert final_report["outcome"] == "root_cause_identified"
    assert ended_at is not None

    tool_rows = admin_conn.execute("SELECT count(*) FROM eval.tool_calls WHERE run_id = %s",
                                   (result.run_id,)).fetchone()[0]
    llm_rows = admin_conn.execute("SELECT count(*) FROM eval.llm_calls WHERE run_id = %s",
                                  (result.run_id,)).fetchone()[0]
    assert tool_rows == result.tool_attempts
    assert llm_rows == result.llm_calls
    # PG-1's rule makes each distinct log query time out once, so attempts exceed calls.
    assert result.tool_attempts > result.tool_calls


async def test_every_fact_and_report_citation_was_returned_to_the_run(pg1, tmp_path):
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain()))
    board = board_for(tmp_path, result.run_id)
    seen = board.seen_evidence()
    facts = board.facts()
    board.close()

    assert len(facts) >= 2
    for fact in facts:
        for evidence_id in fact.evidence_ids:
            assert evidence_id in seen
    cited = []
    for fact in result.report.facts:
        cited.extend(fact.evidence_ids)
    for hypothesis in result.report.hypotheses:
        cited.extend(hypothesis.supporting_evidence_ids)
    assert cited
    for evidence_id in cited:
        assert evidence_id in seen


def llm_statuses(admin_conn, run_id, agent):
    rows = admin_conn.execute(
        "SELECT status FROM eval.llm_calls WHERE run_id = %s AND agent = %s ORDER BY call_id",
        (run_id, agent)).fetchall()
    found = []
    for row in rows:
        found.append(row[0])
    return found


@pytest.mark.parametrize("agent", ["observability", "reporting"])
async def test_invented_evidence_is_rejected_and_repaired(pg1, tmp_path, admin_conn, agent):
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain(invent_evidence_once=agent)))
    assert "invalid_output" in llm_statuses(admin_conn, result.run_id, agent)
    board = board_for(tmp_path, result.run_id)
    seen = board.seen_evidence()
    facts = board.facts()
    board.close()
    assert "L-0badf00d-000001" not in seen
    for fact in facts:
        assert "L-0badf00d-000001" not in fact.evidence_ids
    for fact in result.report.facts:
        assert "L-0badf00d-000001" not in fact.evidence_ids
    assert result.status == "completed"


async def test_a_chained_task_waits_and_builds_on_the_earlier_one(pg1, tmp_path, admin_conn):
    brain = StubBrain(chain_tasks=True)
    seen = []

    def recording(system, prompt, model, number):
        seen.append(prompt)
        return brain(system, prompt, model, number)

    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(recording))
    board = board_for(tmp_path, result.run_id)
    tasks = board.tasks()
    board.close()
    first_round = [task for task in tasks if task.round == 1]
    assert first_round[1].depends_on == [first_round[0].task_id]

    # in round 1 every observability call comes before the first dependency call
    round_one = brain.order[:brain.order.index("reasoning")]
    first_dependency = round_one.index("dependency")
    assert "observability" not in round_one[first_dependency:]
    assert result.stats["dependent_tasks"] >= 1
    joined = "\n".join(seen)
    assert "builds on task %s" % first_round[0].task_id in joined

    stored = admin_conn.execute("SELECT stats FROM eval.runs WHERE run_id = %s",
                                (result.run_id,)).fetchone()[0]
    assert stored["dependent_tasks"] == result.stats["dependent_tasks"]
    assert stored["blocking_objections"] == 1


async def test_a_vague_component_name_is_rejected_and_repaired(pg1, tmp_path, admin_conn):
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain(vague_component_once="reporting")))
    assert "invalid_output" in llm_statuses(admin_conn, result.run_id, "reporting")
    service_names = []
    for service in pg1.evidence.services:
        service_names.append(service.name)
    for hypothesis in result.report.hypotheses:
        assert hypothesis.component in service_names


async def test_a_failing_agent_is_recorded_and_the_run_still_reports(pg1, tmp_path, admin_conn):
    brain = StubBrain(broken_agent="observability")
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(brain))

    board = board_for(tmp_path, result.run_id)
    failed = board.tasks(status="failed")
    board.close()
    assert len(failed) >= 1
    assert failed[0].agent == "observability"
    joined = " ".join(result.notes)
    assert "observability agent failed" in joined
    # three attempts per failed call, never more
    rows = admin_conn.execute(
        "SELECT attempt, status FROM eval.llm_calls WHERE run_id = %s AND agent = 'observability' "
        "ORDER BY call_id", (result.run_id,)).fetchall()
    attempts = []
    for attempt, status in rows:
        attempts.append(attempt)
        assert status == "invalid_output"
    assert max(attempts) == 3
    assert result.report is not None


async def test_a_broken_reporting_agent_gets_a_fallback_report(pg1, tmp_path):
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain(broken_agent="reporting")))
    assert result.report.outcome == "inconclusive"
    assert "the reporting agent did not produce a valid report" in result.report.unresolved
    assert result.report.hypotheses             # the reasoning agent's ranking is kept
    assert result.stats["fallback_reports"] == 1


async def test_an_always_objecting_critic_stops_at_max_rounds(pg1, tmp_path, admin_conn):
    result = await run_investigation(pg1.evidence.incident.incident_id,
                                     config_for(tmp_path, max_rounds=2),
                                     StubBackend(StubBrain(critic_always_objects=True)))
    assert result.status == "max_rounds"
    assert result.rounds == 2
    assert run_row(admin_conn, result.run_id)[0] == "max_rounds"


async def test_a_spent_tool_budget_gives_a_labelled_partial_outcome(pg1, tmp_path, admin_conn):
    result = await run_investigation(
        pg1.evidence.incident.incident_id,
        config_for(tmp_path, gateway=GatewayLimits(max_tool_calls=2, retry_delay_s=0.0)),
        StubBackend(StubBrain()),
    )
    assert result.status == "budget_exhausted"
    assert result.tool_calls == 2
    assert "investigation stopped early (tool_budget)" in result.report.unresolved
    assert run_row(admin_conn, result.run_id)[0] == "budget_exhausted"


async def test_a_spent_llm_budget_stops_the_run_with_a_fallback(pg1, tmp_path):
    result = await run_investigation(
        pg1.evidence.incident.incident_id,
        config_for(tmp_path, llm=LlmLimits(max_llm_calls=3)),
        StubBackend(StubBrain()),
    )
    assert result.status == "budget_exhausted"
    assert result.llm_calls == 3
    assert result.report.outcome == "inconclusive"
    assert "investigation stopped early (llm_budget)" in result.report.unresolved


async def test_agent_prompts_never_contain_answer_key_text(pg1, tmp_path):
    seen_prompts = []
    brain = StubBrain()

    def recording(system, prompt, model, number):
        seen_prompts.append(system + "\n" + prompt)
        return brain(system, prompt, model, number)

    await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                            StubBackend(recording))
    scenario = pg1.truth.scenario
    forbidden = [scenario.plan_id, scenario.archetype, scenario.mechanism, "decoy",
                 "red herring", "incident_plan", "ground truth"]
    assert len(seen_prompts) > 8
    # agents are shown the calls already made (wiring, not just the formatting function)
    shown = False
    for text in seen_prompts:
        if "TOOL CALLS ALREADY MADE IN THIS RUN" in text and " items)" in text:
            shown = True
    assert shown
    for text in seen_prompts:
        for token in forbidden:
            assert token.lower() not in text.lower(), token


async def test_the_agent_to_agent_pattern_runs_end_to_end_and_keeps_a_history(pg1, tmp_path):
    from investigator.web.run_views import read_conversation

    config = config_for(tmp_path, pattern="exchange")
    result = await run_investigation(pg1.evidence.incident.incident_id, config, StubBackend(StubBrain()))
    assert result.status in ["completed", "max_rounds"]
    assert result.report.outcome == "root_cause_identified"
    history = read_conversation(tmp_path / "runs" / result.run_id / "blackboard.sqlite")
    kinds = []
    for entry in history:
        kinds.append(entry["intent"])
    for kind in ["task", "tools", "facts", "ranking", "verdict", "report"]:
        assert kind in kinds, kind


async def test_only_a_rerun_is_told_which_metrics_exist(pg1):
    from investigator.runner import read_brief

    incident_id = pg1.evidence.incident.incident_id
    assert "metrics" not in read_brief(incident_id)
    listed = read_brief(incident_id, True)["metrics"]
    assert len(listed) > 0
    for service, names in listed:
        assert isinstance(service, str)
        assert len(names) > 0


async def test_the_single_agent_keeps_a_history(pg1, tmp_path):
    from investigator.web.run_views import read_conversation

    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain()), system="single_agent")
    history = read_conversation(tmp_path / "runs" / result.run_id / "history.sqlite")
    kinds = []
    for entry in history:
        kinds.append(entry["intent"])
    assert "tools" in kinds
    assert kinds[-1] == "report"
