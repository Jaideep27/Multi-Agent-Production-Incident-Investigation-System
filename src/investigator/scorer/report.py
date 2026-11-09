"""The results table: scored runs per system, side by side.

    python -m investigator.scorer.report --label eval-v1 [--out results/eval-v1.md]

Reads eval.* and truth.scenarios as scorer_ro (it shows each incident's plan ID and true
category). Only runs answered by the real CLI backend are included unless asked otherwise.

Metrics (docs/implementation_plan.md §6): root-cause accuracy over all runs and over
identifiable incidents; inconclusive handling (correct abstentions / inconclusive incidents);
grounding rate (returned / cited evidence IDs, pooled); mean decisive recall; decoy-hit rate;
decoy evidence cited as support; calibration (Brier score, mean confidence when right and
when wrong); mean cost (tool calls, LLM calls, tokens, latency).
"""

import argparse
import math
import sys
from pathlib import Path

import psycopg

from investigator import db

SYSTEMS = ["multi_agent", "single_agent"]

# Counters the team's runs record in eval.runs.stats (the baseline has none of these).
COLLABORATION_KEYS = ["blocking_objections", "critic_requested_tool_calls",
                      "top_hypothesis_changes", "dependent_tasks", "critic_approvals",
                      "follow_up_tasks", "blocking_downgraded"]


def mean(values):
    if not values:
        return None
    total = 0.0
    for value in values:
        total = total + value
    return total / len(values)


def p95(values):
    """Nearest-rank 95th percentile (no interpolation): the value 95% of runs stay within."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(0.95 * len(ordered))
    return ordered[rank - 1]


def summarise(rows):
    summary = {}
    for system in SYSTEMS:
        mine = []
        for row in rows:
            if row["system"] == system:
                mine.append(row)
        if not mine:
            continue

        correct = []
        committed = []
        identifiable_correct = []
        inconclusive_total = 0
        inconclusive_right = 0
        cited = 0
        existing = 0
        recalls = []
        decoys = []
        as_support = 0
        brier_terms = []
        confidence_right = []
        confidence_wrong = []
        tools = []
        llm = []
        tokens_in = []
        tokens_out = []
        tokens_total = []
        latency = []
        attempts = []
        for row in mine:
            hit = 1.0 if row["root_cause_correct"] else 0.0
            correct.append(hit)
            # root_cause_correct alone also counts a right top guess in an inconclusive report.
            if row["root_cause_correct"] and row["outcome_correct"]:
                committed.append(1.0)
            else:
                committed.append(0.0)
            if row["expected_outcome"] == "identifiable":
                identifiable_correct.append(hit)
            else:
                inconclusive_total = inconclusive_total + 1
                if row["outcome_correct"]:
                    inconclusive_right = inconclusive_right + 1
            cited = cited + row["grounding_cited"]
            existing = existing + row["grounding_existing"]
            if row["decisive_recall"] is not None:
                recalls.append(row["decisive_recall"])
            decoys.append(1.0 if row["decoy_hit"] else 0.0)
            as_support = as_support + row["decoys_cited_as_support"]
            if row["confidence"] is not None:
                brier_terms.append((row["confidence"] - hit) ** 2)
                if hit:
                    confidence_right.append(row["confidence"])
                else:
                    confidence_wrong.append(row["confidence"])
            tools.append(row["tool_call_count"])
            llm.append(row["llm_call_count"])
            tokens_in.append(row["input_tokens"])
            tokens_out.append(row["output_tokens"])
            tokens_total.append(row["input_tokens"] + row["output_tokens"])
            latency.append(row["latency_ms"] / 1000.0)
            # Every attempt the gateway made, retries included (eval.tool_calls rows).
            if row.get("tool_attempts") is not None:
                attempts.append(row["tool_attempts"])

        collaboration = {}
        for key in COLLABORATION_KEYS:
            total = 0
            for row in mine:
                stats = row.get("stats") or {}
                total = total + stats.get(key, 0)
            collaboration[key] = total

        grounding_rate = None
        if cited:
            grounding_rate = existing / cited
        summary[system] = {
            "blocking_objections": collaboration["blocking_objections"],
            "critic_requested_tool_calls": collaboration["critic_requested_tool_calls"],
            "top_hypothesis_changes": collaboration["top_hypothesis_changes"],
            "dependent_tasks": collaboration["dependent_tasks"],
            "critic_approvals": collaboration["critic_approvals"],
            "follow_up_tasks": collaboration["follow_up_tasks"],
            "blocking_downgraded": collaboration["blocking_downgraded"],
            "runs": len(mine),
            "root_cause_accuracy": mean(correct),
            "root_cause_right": int(sum(correct)),
            "committed_accuracy": mean(committed),
            "identifiable_accuracy": mean(identifiable_correct),
            "inconclusive_handled": "%d/%d" % (inconclusive_right, inconclusive_total),
            "grounding_rate": grounding_rate,
            "decisive_recall": mean(recalls),
            "decoy_hit_rate": mean(decoys),
            "decoys_cited_as_support": as_support,
            "brier": mean(brier_terms),
            "mean_confidence_correct": mean(confidence_right),
            "mean_confidence_wrong": mean(confidence_wrong),
            "mean_tool_calls": mean(tools),
            "mean_llm_calls": mean(llm),
            "mean_input_tokens": mean(tokens_in),
            "mean_output_tokens": mean(tokens_out),
            "mean_total_tokens": mean(tokens_total),
            "mean_latency_s": mean(latency),
            "p95_latency_s": p95(latency),
            "mean_tool_attempts": mean(attempts),
        }
    return summary


SUMMARY_LINES = [
    ["Runs", "runs", "%d"],
    ["Root-cause accuracy", "root_cause_accuracy", "%.0f%%"],
    ["Right and committed to it", "committed_accuracy", "%.0f%%"],
    ["Accuracy on identifiable incidents", "identifiable_accuracy", "%.0f%%"],
    ["Inconclusive incidents handled", "inconclusive_handled", "%s"],
    ["Grounding (returned / cited IDs)", "grounding_rate", "%.0f%%"],
    ["Decisive-evidence recall", "decisive_recall", "%.0f%%"],
    ["Decoy-hit rate", "decoy_hit_rate", "%.0f%%"],
    ["Decoy evidence cited as support", "decoys_cited_as_support", "%d"],
    ["Brier score (lower is better)", "brier", "%.3f"],
    ["Mean confidence when right", "mean_confidence_correct", "%.2f"],
    ["Mean confidence when wrong", "mean_confidence_wrong", "%.2f"],
    ["Tool calls per run", "mean_tool_calls", "%.1f"],
    ["Tool attempts incl. retries per run", "mean_tool_attempts", "%.1f"],
    ["LLM calls per run", "mean_llm_calls", "%.1f"],
    ["Input tokens per run", "mean_input_tokens", "%.0f"],
    ["Output tokens per run", "mean_output_tokens", "%.0f"],
    ["Total tokens per run", "mean_total_tokens", "%.0f"],
    ["Latency per run (s)", "mean_latency_s", "%.0f"],
    ["Latency p95 (s)", "p95_latency_s", "%.0f"],
    ["Blocking Critic objections", "blocking_objections", "%d"],
    ["Tool calls the Critic asked for", "critic_requested_tool_calls", "%d"],
    ["Top hypothesis changed after a critique", "top_hypothesis_changes", "%d"],
    ["Tasks that waited for another agent", "dependent_tasks", "%d"],
    ["Runs where the Critic approved", "critic_approvals", "%d"],
    ["Follow-up tasks routed by the Critic", "follow_up_tasks", "%d"],
    ["Repeat blocking objections downgraded", "blocking_downgraded", "%d"],
]

PERCENT_KEYS = ["root_cause_accuracy", "committed_accuracy", "identifiable_accuracy", "grounding_rate",
                "decisive_recall", "decoy_hit_rate"]


def format_value(key, template, value):
    if value is None:
        return "-"
    if key in PERCENT_KEYS:
        return template % (value * 100)
    return template % value


def outcome_text(row):
    if row is None:
        return "-"
    mark = "right" if row["root_cause_correct"] else "wrong"
    confidence = "-" if row["confidence"] is None else "%.2f" % row["confidence"]
    decoy = ", decoy" if row["decoy_hit"] else ""
    return "%s (%s%s; %s)" % (mark, confidence, decoy, row["status"])


def render_markdown(rows):
    summary = summarise(rows)
    systems = []
    for system in SYSTEMS:
        if system in summary:
            systems.append(system)

    lines = ["| Metric | " + " | ".join(systems) + " |",
             "|---|" + "---|" * len(systems)]
    for label, key, template in SUMMARY_LINES:
        cells = []
        for system in systems:
            cells.append(format_value(key, template, summary[system][key]))
        lines.append("| %s | %s |" % (label, " | ".join(cells)))

    lines.append("")
    lines.append("| Incident | True category | Expected | " + " | ".join(systems) + " |")
    lines.append("|---|---|---|" + "---|" * len(systems))
    plan_ids = []
    for row in rows:
        if row["plan_id"] not in plan_ids:
            plan_ids.append(row["plan_id"])
    plan_ids.sort()
    for plan_id in plan_ids:
        by_system = {}
        category = ""
        expected = ""
        for row in rows:
            if row["plan_id"] == plan_id:
                by_system[row["system"]] = row
                category = row["category"]
                expected = row["expected_outcome"]
        cells = []
        for system in systems:
            cells.append(outcome_text(by_system.get(system)))
        lines.append("| %s | %s | %s | %s |" % (plan_id, category, expected, " | ".join(cells)))
    return "\n".join(lines) + "\n"


def load_rows(label, include_backends=None):
    """Latest scored run per (incident, system) with this label, as dicts."""
    backends = ["ClaudeCliBackend"]
    if include_backends:
        backends = list(include_backends)
    connection = psycopg.connect(db.conninfo_for("scorer_ro"), autocommit=True)
    try:
        cursor = connection.execute(
            """
            SELECT DISTINCT ON (r.incident_id, r.system)
                   r.system, t.plan_id, t.root_category, t.expected_outcome, r.status,
                   s.root_cause_correct, s.outcome_correct, s.grounding_cited,
                   s.grounding_existing, s.decisive_recall, s.decoy_hit,
                   s.decoys_cited_as_support, s.confidence, s.tool_call_count,
                   s.llm_call_count, s.input_tokens, s.output_tokens, s.latency_ms, r.stats,
                   (SELECT count(*) FROM eval.tool_calls c WHERE c.run_id = r.run_id)
              FROM eval.runs r
              JOIN eval.scores s ON s.run_id = r.run_id
              JOIN truth.scenarios t ON t.incident_id = r.incident_id
             WHERE r.config->>'label' = %s AND r.config->>'backend' = ANY(%s)
             ORDER BY r.incident_id, r.system, r.started_at DESC
            """,
            (label, backends),
        )
        names = ["system", "plan_id", "category", "expected_outcome", "status",
                 "root_cause_correct", "outcome_correct", "grounding_cited",
                 "grounding_existing", "decisive_recall", "decoy_hit",
                 "decoys_cited_as_support", "confidence", "tool_call_count", "llm_call_count",
                 "input_tokens", "output_tokens", "latency_ms", "stats", "tool_attempts"]
        rows = []
        for values in cursor.fetchall():
            row = {}
            for index in range(len(names)):
                row[names[index]] = values[index]
            rows.append(row)
    finally:
        connection.close()
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Results table for one evaluation label.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    text = render_markdown(load_rows(args.label))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
