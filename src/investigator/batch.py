"""Run and score many investigations, resumably.

    python -m investigator.batch --label eval-v1 --incidents inc_a inc_b ...
        [--systems multi_agent single_agent] [--model haiku]

For each incident and system, in order: skip if a finished run with the same label, model and
backend already exists; otherwise run it, then score it. A run that fails is reported and the
batch carries on, so a usage limit or a crash costs at most the run in progress. LLM replies
are cached, so even a repeated run spends no quota on calls it already made.

Takes incident IDs, not plan IDs: only the generator knows the plan.
"""

import argparse
import asyncio
import json
import sys

import psycopg

from investigator import db
from investigator.runner import RunConfig, run_investigation
from investigator.scorer.store import score_run

FINISHED = ["completed", "max_rounds", "budget_exhausted"]


def finished_run_exists(incident_id, system, config, backend_name):
    connection = psycopg.connect(db.conninfo_for("harness_rw"), autocommit=True)
    try:
        row = connection.execute(
            """
            SELECT 1 FROM eval.runs
             WHERE incident_id = %s AND system = %s AND model = %s AND status = ANY(%s)
               AND config->>'label' = %s AND config->>'backend' = %s
             LIMIT 1
            """,
            (incident_id, system, config.model, FINISHED, config.label, backend_name),
        ).fetchone()
    finally:
        connection.close()
    return row is not None


async def run_batch(incident_ids, systems, config, make_backend, on_result=None):
    """make_backend() returns a fresh LLM backend for each run. Returns one dict per run.
    on_result(line) is called as each run finishes, so a batch killed half-way has still
    reported every run it completed."""
    results = ReportingList(on_result)
    for incident_id in incident_ids:
        for system in systems:
            backend = make_backend()
            backend_name = type(backend).__name__
            line = {"incident_id": incident_id, "system": system}
            exists = await asyncio.to_thread(finished_run_exists, incident_id, system, config,
                                             backend_name)
            if exists:
                line["action"] = "skipped"
                results.append(line)
                continue
            try:
                result = await run_investigation(incident_id, config, backend, system=system)
                record = await asyncio.to_thread(score_run, result.run_id)
            except Exception as error:
                line["action"] = "failed"
                line["error"] = "%s: %s" % (type(error).__name__, str(error)[:300])
                results.append(line)
                continue
            line["action"] = "ran"
            line["run_id"] = result.run_id
            line["status"] = result.status
            line["root_cause_correct"] = record.root_cause_correct
            line["outcome_correct"] = record.outcome_correct
            line["llm_calls"] = record.llm_call_count
            line["tool_calls"] = record.tool_call_count
            results.append(line)
    return list(results)


class ReportingList(list):
    """A list that also hands each appended line to a callback."""

    def __init__(self, on_result):
        super().__init__()
        self.on_result = on_result

    def append(self, line):
        super().append(line)
        if self.on_result is not None:
            self.on_result(line)


def print_line(line):
    print(json.dumps(line), flush=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run and score a batch of investigations.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--incidents", nargs="+", required=True)
    parser.add_argument("--systems", nargs="+", default=["multi_agent", "single_agent"])
    parser.add_argument("--model", default="haiku")
    # record: reuse recorded answers, call the model only on a miss; off: always call it
    parser.add_argument("--cache-mode", default="record", choices=["off", "record", "replay"])
    # off for the official evaluation; the viewer's reruns turn it on
    parser.add_argument("--company-context", action="store_true",
                        help="also give the agents the general background of the company")
    parser.add_argument("--pattern", default="blackboard", choices=["blackboard", "exchange"],
                        help="how the team works together (the single agent ignores this)")
    return parser.parse_args(argv)


def config_from_args(args):
    return RunConfig(model=args.model, label=args.label, cache_mode=args.cache_mode,
                     company_context=args.company_context, pattern=args.pattern)


def main(argv=None):
    args = parse_args(argv)

    from investigator.llm.claude_cli import ClaudeCliBackend

    config = config_from_args(args)
    results = asyncio.run(run_batch(args.incidents, args.systems, config, ClaudeCliBackend,
                                    on_result=print_line))
    failures = 0
    for line in results:
        if line["action"] == "failed":
            failures = failures + 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
