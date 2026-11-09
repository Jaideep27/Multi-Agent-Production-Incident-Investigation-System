"""Write a per-incident Markdown summary of one evaluation label (brief, answer key, traps and
both systems' answers), from the database.

    python scripts/export_incidents.py --label eval-v4 --out results/eval-v4-incidents.md

Reads as scorer_ro (needs IV_PG_* and SCORER_RO_PASSWORD). The answer key is shown: this is a
results document, written after the runs.
"""

import argparse
import sys
from pathlib import Path

from investigator.web.data import DataStore

NOISE = {"conflicting_sources": "conflicting observations", "clock_skew": "clock skew",
         "log_gap": "missing logs", "benign_warnings": "noisy warnings"}


def describe_decoy(connection, evidence_id):
    if evidence_id.startswith("C-"):
        row = connection.execute(
            "SELECT s.name, c.change_type, c.summary FROM evidence.changes c "
            "JOIN evidence.services s ON s.service_id = c.service_id WHERE c.evidence_id = %s",
            (evidence_id,)).fetchone()
        return "%s %s: %s" % (row[0], row[1], row[2])
    if evidence_id.startswith("P-"):
        row = connection.execute("SELECT title FROM evidence.past_incidents WHERE evidence_id = %s",
                                 (evidence_id,)).fetchone()
        return "past incident: " + row[0]
    if evidence_id.startswith("L-"):
        row = connection.execute(
            "SELECT s.name, l.message FROM evidence.log_entries l "
            "JOIN evidence.services s ON s.service_id = l.service_id WHERE l.evidence_id = %s",
            (evidence_id,)).fetchone()
        return "%s log: %s" % (row[0], row[1])
    row = connection.execute(
        "SELECT s.name, m.metric_name FROM evidence.metric_series m "
        "JOIN evidence.services s ON s.service_id = m.service_id WHERE m.evidence_id = %s",
        (evidence_id,)).fetchone()
    return "%s metric %s" % (row[0], row[1])


def system_line(label, run):
    if run is None:
        return "- **%s:** not run" % label
    verdict = "right" if run["correct"] else "wrong"
    said = "inconclusive, top guess" if run["outcome"] == "inconclusive" else "identified"
    confidence = "-" if run["confidence"] is None else "%.2f" % run["confidence"]
    return ("- **%s:** %s - %s `%s` (confidence %s) - %d tool calls, %d LLM calls, %dK input "
            "tokens, %d s" % (label, verdict, said, run["top_component"], confidence,
                              run["tool_calls"], run["llm_calls"], run["input_tokens"] // 1000,
                              round(run["latency_s"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Per-incident results summary.")
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    store = DataStore(args.label)
    evaluation = store.evaluation()
    connection = store.connect()
    lines = ["# The evaluation incidents (label %s)" % args.label, "",
             "From the demo database. Each system saw only the brief and the five MCP tools; the",
             "answer key below was hidden from both. Right = exact service and category.", ""]
    try:
        for incident in evaluation["incidents"]:
            truth = store.truth(connection, incident["incident_id"])
            conditions = store.conditions(connection, incident["incident_id"])
            decoys = []
            for decoy in truth["decoys"]:
                decoys.append("%s *(%s)*" % (describe_decoy(connection, decoy["evidence_id"]),
                                             decoy["decoy_type"].replace("_", " ")))
            noise = []
            for item in conditions["noise"]:
                name = NOISE.get(item["noise_type"], item["noise_type"])
                if name not in noise:
                    noise.append(name)
            failures = []
            for tool in conditions["tool_failures"]:
                failures.append("%s (%s %s)" % (tool["tool_name"], tool["rules"].get("mode", ""),
                                                tool["rules"].get("status", "")))
            expected = truth["expected_outcome"]
            if truth["unidentifiable_reason"]:
                expected = expected + " - " + truth["unidentifiable_reason"]
            lines.append("## %s - %s" % (incident["plan_id"], incident["domain"].replace("_", " ")))
            lines.append("- **Brief:** " + incident["summary"])
            lines.append("- **True root cause:** `%s` - %s: %s" % (
                truth["root_component"], truth["root_category"].replace("_", " "), truth["mechanism"]))
            lines.append("- **Expected outcome:** " + expected)
            lines.append("- **Misleading evidence:** " + ("; ".join(decoys) or "none"))
            lines.append("- **Noise:** %s - **Tool failures:** %s" % (", ".join(noise) or "none",
                                                                      ", ".join(failures) or "none"))
            lines.append(system_line("Team", incident["runs"].get("multi_agent")))
            lines.append(system_line("Single agent", incident["runs"].get("single_agent")))
            lines.append("")
    finally:
        connection.close()
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print("wrote " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
