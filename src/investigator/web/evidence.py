"""Evidence in plain words, and how much a metric moved."""


def describe_evidence(connection, evidence_id):
    """One line of plain text for an evidence ID (a log message, a change, a metric...)."""
    prefix = evidence_id[:1]
    row = None
    if prefix == "L":
        row = connection.execute(
            "SELECT s.name || ' ' || l.level || ': ' || l.message FROM evidence.log_entries l "
            "JOIN evidence.services s ON s.service_id = l.service_id WHERE l.evidence_id = %s",
            (evidence_id,)).fetchone()
    elif prefix == "C":
        row = connection.execute(
            "SELECT c.summary || ' (' || c.change_type || ')' FROM evidence.changes c "
            "WHERE c.evidence_id = %s", (evidence_id,)).fetchone()
    elif prefix == "M":
        row = connection.execute(
            "SELECT s.name || ' metric ' || m.metric_name || ' (' || m.unit || ')' "
            "FROM evidence.metric_series m JOIN evidence.services s ON s.service_id = m.service_id "
            "WHERE m.evidence_id = %s", (evidence_id,)).fetchone()
    elif prefix == "P":
        row = connection.execute(
            "SELECT 'past incident: ' || title FROM evidence.past_incidents WHERE evidence_id = %s",
            (evidence_id,)).fetchone()
    elif prefix == "D":
        row = connection.execute(
            "SELECT a.name || ' calls ' || b.name || ' ' || d.config::text "
            "FROM evidence.service_dependencies d "
            "JOIN evidence.services a ON a.service_id = d.caller_id "
            "JOIN evidence.services b ON b.service_id = d.callee_id WHERE d.evidence_id = %s",
            (evidence_id,)).fetchone()
    if row is None:
        return evidence_id
    return row[0]


def cited_in_report(report):
    """Every evidence ID a final report cites, the same way the scorer counts them."""
    ids = []
    for fact in report.get("facts") or []:
        ids.extend(fact.get("evidence_ids") or [])
    for hypothesis in report.get("hypotheses") or []:
        ids.extend(hypothesis.get("supporting_evidence_ids") or [])
        ids.extend(hypothesis.get("contradicting_evidence_ids") or [])
    for action in report.get("recommended_actions") or []:
        ids.extend(action.get("evidence_ids") or [])
    return ids


def key_evidence_of_run(report, decisive_described):
    """The answer key's decisive evidence, marked found if this run's report cites it."""
    cited = cited_in_report(report or {})
    marked = []
    for item in decisive_described:
        marked.append({"evidence_id": item["evidence_id"], "text": item["text"],
                       "found": item["evidence_id"] in cited})
    return marked


def relative_shift(values):
    """How far a series moves away from its early level, relative to that level.

    Used only to pick which chart to show first (for example a latency jump from 5 ms to
    500 ms scores about 99). Baseline = median of the first fifth of the points.
    """
    if len(values) < 5:
        return 0.0
    early_count = len(values) // 5
    early = sorted(values[:early_count])
    baseline = early[len(early) // 2]
    largest = 0.0
    for value in values:
        distance = abs(value - baseline)
        if distance > largest:
            largest = distance
    scale = abs(baseline)
    if scale < 1.0:
        scale = 1.0
    return largest / scale


def empty_conditions():
    return {"decoys": 0, "noise": [], "tool_failures": []}
