"""Score finished runs into eval.scores.

    python -m investigator.scorer --run <run_id> [--run <run_id> ...]
    python -m investigator.scorer --all        # every finished run without a score

Needs SCORER_RO_PASSWORD and HARNESS_RW_PASSWORD (plus IV_PG_*) in the environment.
"""

import argparse
import sys

import psycopg

from investigator import db
from investigator.scorer.store import score_run


def unscored_runs():
    connection = psycopg.connect(db.conninfo_for("scorer_ro"), autocommit=True)
    try:
        rows = connection.execute(
            """
            SELECT r.run_id::text FROM eval.runs r
             WHERE r.status <> 'running' AND r.final_report IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM eval.scores s WHERE s.run_id = r.run_id)
             ORDER BY r.started_at
            """
        ).fetchall()
    finally:
        connection.close()
    ids = []
    for row in rows:
        ids.append(row[0])
    return ids


def main(argv=None):
    parser = argparse.ArgumentParser(description="Score finished investigation runs.")
    parser.add_argument("--run", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    run_ids = list(args.run)
    if args.all:
        for run_id in unscored_runs():
            if run_id not in run_ids:
                run_ids.append(run_id)
    if not run_ids:
        print("nothing to score")
        return 0

    for run_id in run_ids:
        record = score_run(run_id)
        confidence = "-"
        if record.confidence is not None:
            confidence = "%.2f" % record.confidence
        print("%s root_cause_correct=%s outcome_correct=%s grounded=%d/%d decisive_recall=%s "
              "decoy_hit=%s decoys_as_support=%d confidence=%s tool_calls=%d llm_calls=%d "
              "tokens_in=%d tokens_out=%d latency_ms=%d"
              % (run_id, record.root_cause_correct, record.outcome_correct,
                 record.grounding_existing, record.grounding_cited, record.decisive_recall,
                 record.decoy_hit, record.decoys_cited_as_support, confidence,
                 record.tool_call_count, record.llm_call_count, record.input_tokens,
                 record.output_tokens, record.latency_ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
