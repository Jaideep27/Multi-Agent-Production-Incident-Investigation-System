"""How a finished run is shown: its steps, its rounds, its blackboard and its limits."""

import json
import sqlite3
from pathlib import Path


def split_into_steps(llm_calls, tool_calls):
    """Give each tool call to the LLM turn that requested it.

    A tool call belongs to the last model reply that finished before the call started.
    This is exact for the single agent (one turn at a time); for the team, parallel
    specialists make it approximate, so the team view groups tool calls by round instead.
    """
    steps = []
    for call in llm_calls:
        steps.append({"agent": call["agent"], "attempt": call["attempt"],
                      "status": call["status"], "end_s": call["end_s"], "tool_calls": []})
    for tool_call in tool_calls:
        start = tool_call["end_s"] - (tool_call["latency_ms"] or 0) / 1000.0
        owner = None
        for step in steps:
            if step["end_s"] <= start + 0.001:
                owner = step
        if owner is not None:
            owner["tool_calls"].append(tool_call)
    return steps


def tool_calls_by_round(llm_calls, tool_calls):
    """Round boundaries are the Orchestrator's first-attempt replies."""
    starts = []
    for call in llm_calls:
        if call["agent"] == "orchestrator" and call["attempt"] == 1:
            starts.append(call["end_s"])
    counts = []
    for index in range(len(starts)):
        counts.append({"round": index + 1, "calls": 0, "failed_attempts": 0, "empty": 0})
    for tool_call in tool_calls:
        position = -1
        for index in range(len(starts)):
            if starts[index] <= tool_call["end_s"]:
                position = index
        if position < 0:
            continue
        entry = counts[position]
        entry["calls"] = entry["calls"] + 1
        if tool_call["status"] in ("timeout", "error"):
            entry["failed_attempts"] = entry["failed_attempts"] + 1
        if tool_call["status"] == "empty":
            entry["empty"] = entry["empty"] + 1
    return counts




def read_blackboard(path):
    """Tasks, facts, hypothesis revisions and objections from a finished team run."""
    path = Path(path)
    if not path.exists():
        return None
    connection = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    try:
        tasks = []
        for row in connection.execute(
                "SELECT task_id, agent, goal, depends_on, status, round FROM tasks ORDER BY seq"):
            tasks.append({"task_id": row[0], "agent": row[1], "goal": row[2],
                          "depends_on": json.loads(row[3] or "[]"), "status": row[4],
                          "round": row[5]})
        facts = []
        for row in connection.execute(
                "SELECT fact_id, kind, statement, evidence_ids, author, round "
                "FROM facts ORDER BY seq"):
            facts.append({"fact_id": row[0], "kind": row[1], "statement": row[2],
                          "evidence_ids": json.loads(row[3] or "[]"), "author": row[4],
                          "round": row[5]})
        hypotheses = []
        for row in connection.execute(
                "SELECT hypothesis_id, round, body FROM hypothesis_revisions ORDER BY seq"):
            body = json.loads(row[2])
            body["hypothesis_id"] = row[0]
            body["round"] = row[1]
            hypotheses.append(body)
        objections = []
        for row in connection.execute("SELECT body, resolved FROM objections ORDER BY seq"):
            body = json.loads(row[0])
            body["resolved"] = bool(row[1])
            objections.append(body)
    finally:
        connection.close()
    return {"tasks": tasks, "facts": facts, "hypotheses": hypotheses, "objections": objections}


def read_conversation(path):
    """The agents' conversation, in order. Safe to call while the run is still writing it."""
    path = Path(path)
    if not path.exists():
        return []
    connection = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    try:
        try:
            rows = connection.execute("SELECT sender, recipient, intent, text, evidence_ids, round "
                                      "FROM messages ORDER BY seq").fetchall()
        except sqlite3.OperationalError:
            return []       # a run from before conversations were kept
    finally:
        connection.close()
    history = []
    for sender, recipient, intent, text, evidence_ids, round_number in rows:
        history.append({"sender": sender, "recipient": recipient, "intent": intent, "text": text,
                        "evidence_ids": json.loads(evidence_ids or "[]"), "round": round_number})
    return history


SYSTEMS_SHOWN = ["multi_agent", "single_agent"]


def newest_per_system(labels_newest_first, runs_for_label):
    """The newest finished run of each system, so a case page shows the latest result.

    A rerun may cover only one system; the other system then comes from an earlier run.
    Each run is tagged with the label it came from."""
    shown = {}
    for label in labels_newest_first:
        runs = runs_for_label(label)
        for system in SYSTEMS_SHOWN:
            if system in runs and system not in shown:
                run = runs[system]
                run["label"] = label
                shown[system] = run
        if len(shown) == len(SYSTEMS_SHOWN):
            break
    return shown


def all_finished(runs):
    for system in runs:
        if runs[system]["status"] == "running":
            return False
    return True


def run_limits(config):
    """The budgets a run was given, as recorded in its own config (not hardcoded here)."""
    config = config or {}
    gateway = config.get("gateway") or {}
    llm = config.get("llm") or {}
    return {"model": config.get("model"),
            "max_tool_calls": gateway.get("max_tool_calls"),
            "tool_attempts": gateway.get("max_attempts"),
            "tool_timeout_s": gateway.get("call_timeout_s"),
            "max_llm_calls": llm.get("max_llm_calls"),
            "llm_attempts": llm.get("max_attempts"),
            "max_rounds": config.get("max_rounds"),
            "max_specialist_steps": config.get("max_specialist_steps"),
            "max_baseline_steps": config.get("max_baseline_steps")}
