"""The resumable batch runner and the results report, on the stub LLM."""

import pytest

from investigator.batch import run_batch
from investigator.gateway.gateway import GatewayLimits
from investigator.llm.stub import StubBackend
from investigator.runner import RunConfig
from investigator.scorer.report import load_rows, render_markdown
from tests.stub_brain import StubBrain

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def remove_test_runs(pg1, admin_conn):
    yield
    admin_conn.execute("DELETE FROM eval.runs WHERE incident_id = %s",
                       (pg1.evidence.incident.incident_id,))


def config_for(tmp_path, label):
    return RunConfig(runs_dir=str(tmp_path / "runs"), cache_mode="off", label=label,
                     gateway=GatewayLimits(retry_delay_s=0.0))


async def test_batch_runs_scores_and_then_skips_finished_runs(pg1, tmp_path, admin_conn):
    incident_id = pg1.evidence.incident.incident_id
    config = config_for(tmp_path, "batch-test")

    first = await run_batch([incident_id], ["multi_agent", "single_agent"], config,
                            lambda: StubBackend(StubBrain()))
    actions = []
    for line in first:
        actions.append(line["action"])
    assert actions == ["ran", "ran"]
    scored = admin_conn.execute(
        "SELECT count(*) FROM eval.scores s JOIN eval.runs r USING (run_id) "
        "WHERE r.incident_id = %s AND r.config->>'label' = 'batch-test'", (incident_id,)).fetchone()[0]
    assert scored == 2

    second = await run_batch([incident_id], ["multi_agent", "single_agent"], config,
                             lambda: StubBackend(StubBrain()))
    actions = []
    for line in second:
        actions.append(line["action"])
    assert actions == ["skipped", "skipped"]


async def test_each_run_is_reported_as_soon_as_it_finishes(pg1, tmp_path):
    # A batch that is killed half-way must still have logged the runs it finished.
    incident_id = pg1.evidence.incident.incident_id
    seen_during_batch = []

    def on_result(line):
        seen_during_batch.append(line["system"])

    await run_batch([incident_id], ["multi_agent", "single_agent"],
                    config_for(tmp_path, "progress-test"), lambda: StubBackend(StubBrain()),
                    on_result=on_result)
    assert seen_during_batch == ["multi_agent", "single_agent"]


async def test_another_label_runs_again(pg1, tmp_path):
    incident_id = pg1.evidence.incident.incident_id
    await run_batch([incident_id], ["multi_agent"], config_for(tmp_path, "label-a"),
                    lambda: StubBackend(StubBrain()))
    again = await run_batch([incident_id], ["multi_agent"], config_for(tmp_path, "label-b"),
                            lambda: StubBackend(StubBrain()))
    assert again[0]["action"] == "ran"


async def test_a_failing_run_is_reported_and_the_batch_continues(pg1, tmp_path):
    incident_id = pg1.evidence.incident.incident_id
    results = await run_batch(["inc_0badf00d", incident_id], ["multi_agent"],
                              config_for(tmp_path, "batch-fail"), lambda: StubBackend(StubBrain()))
    assert results[0]["action"] == "failed"
    assert "inc_0badf00d" in results[0]["error"]
    assert results[1]["action"] == "ran"


async def test_report_joins_scores_with_the_answer_key(pg1, tmp_path):
    incident_id = pg1.evidence.incident.incident_id
    await run_batch([incident_id], ["multi_agent", "single_agent"],
                    config_for(tmp_path, "report-test"), lambda: StubBackend(StubBrain()))

    real_only = load_rows("report-test")
    assert real_only == []                     # stub runs are excluded by default
    rows = load_rows("report-test", include_backends=["StubBackend"])
    assert len(rows) == 2
    for found in rows:
        assert found["plan_id"] == "PG-1"
        assert found["category"] == "config_change"
    text = render_markdown(rows)
    assert "| PG-1 |" in text
