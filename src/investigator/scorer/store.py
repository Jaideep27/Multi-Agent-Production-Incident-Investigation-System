"""Score a finished run: read it (and the answer key) as scorer_ro, write eval.scores as
harness_rw. The harness itself never reads the answer key; only this module does."""

import psycopg

from investigator import db
from investigator.models.report import FinalReport
from investigator.models.scores import ScoreRecord
from investigator.scorer.metrics import score_report
from investigator.scorer.truth import truth_for_incident


def read_run_costs(connection, run_id):
    """Logical tool calls are the attempt-1 rows: each executed call has exactly one."""
    tool_calls = connection.execute(
        "SELECT count(*) FILTER (WHERE attempt = 1) FROM eval.tool_calls WHERE run_id = %s",
        (run_id,),
    ).fetchone()[0]
    llm_calls, input_tokens, output_tokens = connection.execute(
        "SELECT count(*), coalesce(sum(input_tokens), 0), coalesce(sum(output_tokens), 0) "
        "FROM eval.llm_calls WHERE run_id = %s",
        (run_id,),
    ).fetchone()
    seen = []
    for row in connection.execute("SELECT evidence_ids FROM eval.tool_calls WHERE run_id = %s",
                                  (run_id,)).fetchall():
        for evidence_id in row[0]:
            if evidence_id not in seen:
                seen.append(evidence_id)
    return tool_calls, llm_calls, int(input_tokens), int(output_tokens), seen


def score_run(run_id):
    scorer = psycopg.connect(db.conninfo_for("scorer_ro"), autocommit=True)
    try:
        row = scorer.execute(
            "SELECT incident_id, status, rounds, final_report, started_at, ended_at "
            "FROM eval.runs WHERE run_id = %s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("no run %s" % run_id)
        incident_id, status, rounds, final_report, started_at, ended_at = row
        if status == "running" or final_report is None or ended_at is None:
            raise ValueError("run %s is not finished (status %s)" % (run_id, status))
        already = scorer.execute("SELECT 1 FROM eval.scores WHERE run_id = %s",
                                 (run_id,)).fetchone()
        if already is not None:
            raise ValueError("run %s is already scored" % run_id)

        truth = truth_for_incident(scorer, incident_id)
        tool_calls, llm_calls, input_tokens, output_tokens, seen = read_run_costs(scorer, run_id)
    finally:
        scorer.close()

    report = FinalReport.model_validate(final_report)
    score = score_report(report, truth, seen)
    record = ScoreRecord(
        run_id=run_id,
        root_cause_correct=score.root_cause_correct,
        outcome_correct=score.outcome_correct,
        grounding_cited=score.grounding_cited,
        grounding_existing=score.grounding_existing,
        decisive_recall=score.decisive_recall,
        decoy_hit=score.decoy_hit,
        decoys_cited_as_support=score.decoys_cited_as_support,
        confidence=score.confidence,
        tool_call_count=tool_calls,
        llm_call_count=llm_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=int((ended_at - started_at).total_seconds() * 1000),
        rounds=rounds,
    )

    harness = psycopg.connect(db.conninfo_for("harness_rw"), autocommit=True)
    try:
        harness.execute(
            """
            INSERT INTO eval.scores
              (run_id, root_cause_correct, outcome_correct, grounding_cited, grounding_existing,
               grounding_supporting, decisive_recall, decoy_hit, decoys_cited_as_support,
               confidence, tool_call_count, llm_call_count, input_tokens, output_tokens,
               latency_ms, rounds)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (str(record.run_id), record.root_cause_correct, record.outcome_correct,
             record.grounding_cited, record.grounding_existing, record.grounding_supporting,
             record.decisive_recall, record.decoy_hit, record.decoys_cited_as_support,
             record.confidence, record.tool_call_count, record.llm_call_count,
             record.input_tokens, record.output_tokens, record.latency_ms, record.rounds),
        )
    finally:
        harness.close()
    return record
