"""Run one investigation of one incident, end to end, and log it to eval.*.

    python -m investigator.runner --incident inc_1a2b3c4d [--model haiku] [--max-rounds 3]

Per run: one eval.runs row (harness_rw), one MCP server subprocess bound to the incident
(agent_ro), one blackboard file, one LlmClient with a record/replay cache. The runner reads
only agent-visible data (the incident brief, as agent_ro) and never the answer key.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

import psycopg
from mcp import Client, StdioServerParameters
from psycopg.types.json import Jsonb
from pydantic import Field

from investigator import db
from investigator.agents import exchange
from investigator.agents.baseline import BaselineContext, run_baseline
from investigator.agents.conversation import Conversation
from investigator.agents.graph import build_graph, recursion_limit
from investigator.agents.team import RunContext
from investigator.blackboard.store import Blackboard
from investigator.company_context import add_background
from investigator.gateway.call_log import PostgresCallLog
from investigator.gateway.caller import McpToolCaller
from investigator.gateway.gateway import Gateway, GatewayLimits
from investigator.llm.cache import ResponseCache
from investigator.llm.call_log import PostgresLlmCallLog
from investigator.llm.client import LlmClient, LlmLimits
from investigator.models.common import StrictModel
from investigator.models.report import FinalReport

INCIDENT_ID_PATTERN = r"^inc_[0-9a-f]{8}$"


class RunConfig(StrictModel):
    model: str = "haiku"
    max_rounds: int = Field(default=3, ge=1, le=5)
    max_specialist_steps: int = Field(default=3, ge=1, le=4)
    max_baseline_steps: int = Field(default=12, ge=1, le=20)
    gateway: GatewayLimits = Field(default_factory=GatewayLimits)
    llm: LlmLimits = Field(default_factory=LlmLimits)
    runs_dir: str = "runs"
    cache_dir: str | None = ".llm_cache"     # git-ignored
    cache_mode: str = Field(default="record", pattern="^(off|record|replay)$")
    label: str = ""          # groups the runs of one evaluation (stored in eval.runs.config)
    company_context: bool = False   # reruns: also give the agents the company background
    pattern: str = Field(default="blackboard", pattern="^(blackboard|exchange)$")  # team only


class RunResult(StrictModel):
    run_id: str
    incident_id: str
    status: str
    rounds: int
    report: FinalReport
    notes: list[str]
    stats: dict
    tool_calls: int
    tool_attempts: int
    llm_calls: int


def read_metric_names(cursor, incident_id):
    """Which metrics exist for each service: names only, so agents need not guess them."""
    cursor.execute(
        'SELECT DISTINCT s.name COLLATE "C" AS service, m.metric_name COLLATE "C" AS metric '
        "FROM evidence.metric_series m JOIN evidence.services s ON s.service_id = m.service_id "
        "WHERE m.incident_id = %s ORDER BY service, metric",
        (incident_id,),
    )
    grouped = []
    for service, metric in cursor.fetchall():
        if not grouped or grouped[-1][0] != service:
            grouped.append([service, []])
        grouped[-1][1].append(metric)
    return grouped


def read_brief(incident_id, with_background=False):
    """The incident report and service list, read as agent_ro (what any agent may see)."""
    connection = psycopg.connect(db.conninfo_for("agent_ro"))
    try:
        with db.incident_transaction(connection, incident_id) as cursor:
            cursor.execute(
                "SELECT summary, reporter, reported_at, window_start, window_end, domain "
                "FROM evidence.incidents WHERE incident_id = %s",
                (incident_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError("incident %s does not exist" % incident_id)
            cursor.execute(
                'SELECT name, role FROM evidence.services WHERE incident_id = %s '
                'ORDER BY name COLLATE "C"',
                (incident_id,),
            )
            services = []
            for name, role in cursor.fetchall():
                services.append([name, role])
            metrics = []
            if with_background:
                metrics = read_metric_names(cursor, incident_id)
    finally:
        connection.close()
    summary, reporter, reported_at, window_start, window_end, domain = row
    brief = {
        "summary": summary,
        "reporter": reporter,
        "reported_at": reported_at.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "services": services,
    }
    if metrics:
        brief["metrics"] = metrics
    return add_background(brief, domain, with_background)


def mcp_server_parameters(incident_id):
    env = dict(os.environ)
    env["INCIDENT_ID"] = incident_id
    return StdioServerParameters(command=sys.executable, args=["-m", "investigator.mcp_server"],
                                 env=env)


async def run_investigation(incident_id, config, backend, system="multi_agent"):
    """system: "multi_agent" (the team) or "single_agent" (the baseline). Both get the same
    config, so the same model, gateway limits and LLM limits."""
    if not re.fullmatch(INCIDENT_ID_PATTERN, incident_id):
        raise ValueError("not an incident ID: %r" % incident_id)
    if system not in ["multi_agent", "single_agent"]:
        raise ValueError("unknown system %r" % system)

    run_id = str(uuid.uuid4())
    recorded_config = config.model_dump(mode="json")
    # Which backend answered: keeps stub runs out of evaluation results.
    recorded_config["backend"] = type(backend).__name__
    sync_harness = await asyncio.to_thread(psycopg.connect, db.conninfo_for("harness_rw"),
                                           autocommit=True)
    harness = db.ThreadedConnection(sync_harness)
    try:
        await harness.execute(
            "INSERT INTO eval.runs (run_id, incident_id, system, model, config, status) "
            "VALUES (%s, %s, %s, %s, %s, 'running')",
            (run_id, incident_id, system, config.model, Jsonb(recorded_config)),
        )
        try:
            result = await investigate(run_id, incident_id, config, backend, harness, system)
        except BaseException:
            await harness.execute(
                "UPDATE eval.runs SET status = 'failed', ended_at = now() WHERE run_id = %s",
                (run_id,),
            )
            raise
        await harness.execute(
            "UPDATE eval.runs SET status = %s, ended_at = now(), rounds = %s, final_report = %s, "
            "stats = %s WHERE run_id = %s",
            (result.status, result.rounds, Jsonb(result.report.model_dump(mode="json")),
             Jsonb(result.stats), run_id),
        )
        return result
    finally:
        await harness.close()


async def investigate(run_id, incident_id, config, backend, harness, system):
    brief = await asyncio.to_thread(read_brief, incident_id, config.company_context)
    run_dir = Path(config.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cache = None
    if config.cache_mode != "off" and config.cache_dir is not None:
        cache = ResponseCache(config.cache_dir, mode=config.cache_mode)

    async with Client(mcp_server_parameters(incident_id)) as client:
        gateway = Gateway(caller=McpToolCaller(client), run_id=run_id, limits=config.gateway,
                          log=PostgresCallLog(harness))
        llm = LlmClient(backend=backend, run_id=run_id, model=config.model, limits=config.llm,
                        log=PostgresLlmCallLog(harness), cache=cache,
                        transcript_path=run_dir / "llm_transcript.jsonl")
        if system == "single_agent":
            history_board = Blackboard.create(run_dir / "history.sqlite")
            try:
                ctx = BaselineContext(brief=brief, llm=llm, gateway=gateway,
                                      max_steps=config.max_baseline_steps,
                                      history=Conversation(history_board))
                await run_baseline(ctx)
            finally:
                history_board.close()
            rounds = ctx.steps_used
            notes = ctx.remarks
        else:
            board = Blackboard.create(run_dir / "blackboard.sqlite")
            try:
                # A fifth of the tool budget is kept for the Critic's follow-up checks
                # (8 of the default 40).
                ctx = RunContext(brief=brief, llm=llm, gateway=gateway, board=board,
                                 max_rounds=config.max_rounds,
                                 max_specialist_steps=config.max_specialist_steps,
                                 tool_reserve=config.gateway.max_tool_calls // 5)
                if config.pattern == "exchange":
                    exchange.configure(ctx)
                    rounds = await exchange.run_exchange(ctx)
                else:
                    graph = build_graph(ctx)
                    final_state = await graph.ainvoke(
                        {"round": 0},
                        config={"recursion_limit": recursion_limit(config.max_rounds)},
                    )
                    rounds = final_state["round"]
            finally:
                board.close()
            notes = ctx.notes

    return RunResult(
        run_id=run_id, incident_id=incident_id, status=ctx.status, rounds=rounds,
        report=ctx.report, notes=notes, stats=ctx.stats,
        tool_calls=gateway.logical_calls, tool_attempts=gateway.physical_attempts,
        llm_calls=llm.calls_made,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Investigate one incident with the agent team.")
    parser.add_argument("--incident", required=True)
    parser.add_argument("--system", default="multi_agent", choices=["multi_agent", "single_agent"])
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--cache-mode", default="record", choices=["off", "record", "replay"])
    parser.add_argument("--cache-dir", default=".llm_cache")
    parser.add_argument("--runs-dir", default="runs")
    args = parser.parse_args(argv)

    from investigator.llm.claude_cli import ClaudeCliBackend

    config = RunConfig(model=args.model, max_rounds=args.max_rounds, cache_mode=args.cache_mode,
                       cache_dir=args.cache_dir, runs_dir=args.runs_dir)
    result = asyncio.run(run_investigation(args.incident, config, ClaudeCliBackend(),
                                           system=args.system))
    report_path = Path(args.runs_dir) / result.run_id / "report.json"
    report_path.write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
    print("%s run %s: %s after %d round(s)/step(s); %d tool calls (%d attempts), %d LLM calls"
          % (args.system, result.run_id, result.status, result.rounds, result.tool_calls, result.tool_attempts,
             result.llm_calls))
    print("outcome: %s" % result.report.outcome)
    rank = 1
    for hypothesis in result.report.hypotheses:
        print("  %d. %s / %s (%.2f)" % (rank, hypothesis.component, hypothesis.category,
                                        hypothesis.confidence))
        rank = rank + 1
    print("report written to %s" % report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
