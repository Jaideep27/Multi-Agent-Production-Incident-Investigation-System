"""The single-agent baseline on the stub LLM, and scoring both systems' runs into eval.scores."""

import pytest

from investigator.gateway.gateway import GatewayLimits
from investigator.llm.stub import StubBackend
from investigator.runner import RunConfig, run_investigation
from investigator.scorer.store import score_run
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


async def test_baseline_run_completes_and_is_logged_as_single_agent(pg1, tmp_path, admin_conn):
    brain = StubBrain()
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(brain), system="single_agent")
    assert result.status == "completed"
    assert list(brain.calls_by_agent.keys()) == ["investigator"]
    row = admin_conn.execute("SELECT system, status, config->>'backend' FROM eval.runs "
                             "WHERE run_id = %s", (result.run_id,)).fetchone()
    # the backend is recorded so stub runs can never be mistaken for real ones
    assert row == ("single_agent", "completed", "StubBackend")
    tools = admin_conn.execute("SELECT DISTINCT tool FROM eval.tool_calls WHERE run_id = %s",
                               (result.run_id,)).fetchall()
    assert len(tools) >= 2
    assert result.report.outcome == "root_cause_identified"


async def test_baseline_report_cites_only_returned_evidence(pg1, tmp_path, admin_conn):
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain(invent_evidence_once="investigator")),
                                     system="single_agent")
    statuses = admin_conn.execute(
        "SELECT status FROM eval.llm_calls WHERE run_id = %s ORDER BY call_id",
        (result.run_id,)).fetchall()
    assert ("invalid_output",) in statuses
    returned = []
    for row in admin_conn.execute("SELECT evidence_ids FROM eval.tool_calls WHERE run_id = %s",
                                  (result.run_id,)).fetchall():
        returned.extend(row[0])
    for fact in result.report.facts:
        for evidence_id in fact.evidence_ids:
            assert evidence_id in returned


async def test_baseline_obeys_the_same_tool_budget(pg1, tmp_path):
    result = await run_investigation(
        pg1.evidence.incident.incident_id,
        config_for(tmp_path, gateway=GatewayLimits(max_tool_calls=2, retry_delay_s=0.0)),
        StubBackend(StubBrain()), system="single_agent")
    assert result.tool_calls == 2
    assert result.status == "budget_exhausted"
    assert "investigation stopped early (tool_budget)" in result.report.unresolved


async def test_scoring_both_systems_writes_one_row_each(pg1, tmp_path, admin_conn):
    incident_id = pg1.evidence.incident.incident_id
    multi = await run_investigation(incident_id, config_for(tmp_path), StubBackend(StubBrain()))
    single = await run_investigation(incident_id, config_for(tmp_path), StubBackend(StubBrain()),
                                     system="single_agent")

    for result in [multi, single]:
        record = score_run(result.run_id)
        stored = admin_conn.execute(
            "SELECT root_cause_correct, outcome_correct, grounding_cited, grounding_existing, "
            "decoy_hit, decoys_cited_as_support, tool_call_count, llm_call_count, input_tokens, "
            "rounds FROM eval.scores WHERE run_id = %s", (result.run_id,)).fetchone()
        assert stored is not None
        assert stored[1] is True                       # identifiable -> identified
        assert stored[2] >= 1
        assert stored[3] == stored[2]                  # every citation was returned
        assert stored[6] == result.tool_calls          # logical calls (attempt 1 rows)
        assert stored[7] == result.llm_calls
        assert stored[8] > 0
        assert stored[9] == result.rounds
        assert record.tool_call_count == result.tool_calls


async def test_scoring_twice_is_refused(pg1, tmp_path):
    result = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain()))
    score_run(result.run_id)
    with pytest.raises(ValueError, match="already scored"):
        score_run(result.run_id)


async def test_cli_scores_every_finished_unscored_run(pg1, tmp_path, admin_conn, capsys):
    from investigator.scorer.__main__ import main

    first = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                    StubBackend(StubBrain()))
    second = await run_investigation(pg1.evidence.incident.incident_id, config_for(tmp_path),
                                     StubBackend(StubBrain()), system="single_agent")
    score_run(first.run_id)

    assert main(["--all"]) == 0
    output = capsys.readouterr().out
    assert second.run_id in output
    assert first.run_id not in output          # already scored, so skipped
    count = admin_conn.execute("SELECT count(*) FROM eval.scores WHERE run_id IN (%s, %s)",
                               (first.run_id, second.run_id)).fetchone()[0]
    assert count == 2


async def test_a_running_run_cannot_be_scored(pg1, admin_conn):
    run_id = admin_conn.execute(
        "INSERT INTO eval.runs (incident_id, system, model, status) "
        "VALUES (%s, 'multi_agent', 'test', 'running') RETURNING run_id",
        (pg1.evidence.incident.incident_id,)).fetchone()[0]
    with pytest.raises(ValueError, match="not finished"):
        score_run(str(run_id))
