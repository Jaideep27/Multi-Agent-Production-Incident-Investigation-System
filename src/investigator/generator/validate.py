"""Check a built incident before it is loaded. Returns problems as "code: detail" strings.

The truth checks repeat truth.invariant_violations (004_truth.sql) and use the same names, so
a problem reads the same whether Python or Postgres finds it. The loader runs both.
"""

from datetime import datetime

from investigator.models.common import EVIDENCE_PREFIXES, find_duplicates
from investigator.tools.failures import parse_rules

FAILURE_MODES = ["transient", "persistent", "partial", "probabilistic"]
# Modes that can block evidence for good; an identifiable incident must stay solvable.
BLOCKING_MODES = ["persistent", "probabilistic"]

TOOL_FOR_PREFIX = {
    "L": "query_logs",
    "M": "query_metrics",
    "C": "get_change_history",
    "D": "get_service_dependencies",
    "P": "search_past_incidents",
}

# Words that only make sense in the answer key.
LEAK_WORDS = ["decoy", "red herring", "red_herring", "ground truth", "answer key", "incident_plan"]


# ------------------------------------------------------------------ helpers


def evidence_items(evidence):
    """[kind, item] for every evidence item."""
    items = []
    for item in evidence.dependencies:
        items.append(["dependency", item])
    for item in evidence.logs:
        items.append(["log", item])
    for item in evidence.metrics:
        items.append(["metric", item])
    for item in evidence.changes:
        items.append(["change", item])
    for item in evidence.past_incidents:
        items.append(["past_incident", item])
    return items


def all_evidence_ids(evidence):
    ids = []
    for kind, item in evidence_items(evidence):
        ids.append(item.evidence_id)
    return ids


def collect_strings(value, found):
    """Every string inside a nested structure of dicts and lists, keys included."""
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key in value:
            found.append(str(key))
            collect_strings(value[key], found)
    elif isinstance(value, list):
        for element in value:
            collect_strings(element, found)


# ------------------------------------------------------------------ checks


def check_ids(dataset):
    problems = []
    incident_hash = dataset.evidence.incident.incident_id[len("inc_"):]
    for kind, item in evidence_items(dataset.evidence):
        evidence_id = item.evidence_id
        if not evidence_id.startswith(EVIDENCE_PREFIXES[kind] + "-"):
            problems.append("wrong_prefix: %s is a %s" % (evidence_id, kind))
        parts = evidence_id.split("-")
        if len(parts) != 3 or parts[1] != incident_hash:
            problems.append("foreign_incident_hash: %s" % evidence_id)
    for evidence_id in find_duplicates(all_evidence_ids(dataset.evidence)):
        problems.append("duplicate_evidence_id: %s" % evidence_id)
    return problems


def check_services(dataset):
    evidence = dataset.evidence
    names = []
    for service in evidence.services:
        names.append(service.name)

    problems = []
    for name in find_duplicates(names):
        problems.append("duplicate_service: %s" % name)

    used = []
    for edge in evidence.dependencies:
        used.append([edge.evidence_id, edge.caller])
        used.append([edge.evidence_id, edge.callee])
        if edge.caller == edge.callee:
            problems.append("self_dependency: %s" % edge.evidence_id)
    for item in evidence.logs:
        used.append([item.evidence_id, item.service])
    for item in evidence.metrics:
        used.append([item.evidence_id, item.service])
    for item in evidence.changes:
        used.append([item.evidence_id, item.service])

    for evidence_id, name in used:
        if name not in names:
            problems.append("unknown_service: %s uses %s" % (evidence_id, name))

    series_keys = []
    for item in evidence.metrics:
        series_keys.append("%s/%s/%s/%s" % (item.service, item.instance, item.metric_name,
                                            item.source))
    for key in find_duplicates(series_keys):
        problems.append("duplicate_metric_series: %s" % key)
    return problems


def check_times(dataset):
    evidence = dataset.evidence
    start = evidence.incident.window_start
    end = evidence.incident.window_end
    problems = []
    if not start < evidence.incident.reported_at <= end:
        problems.append("outside_window: reported_at")
    for item in evidence.logs:
        if not start <= item.ts <= end:
            problems.append("outside_window: %s" % item.evidence_id)
    for item in evidence.metrics:
        for point in item.points:
            if not start <= point.ts <= end:
                problems.append("outside_window: %s at %s" % (item.evidence_id, point.ts))
    for item in evidence.changes:
        # Changes may predate the window (a deploy hours earlier), but not follow it.
        if item.ts > end:
            problems.append("outside_window: %s" % item.evidence_id)
    return problems


def check_truth_references(dataset):
    ids = all_evidence_ids(dataset.evidence)
    problems = []
    for row in dataset.truth.decisive:
        if row.evidence_id not in ids:
            problems.append("missing_evidence: decisive %s" % row.evidence_id)
    for row in dataset.truth.decoys:
        if row.evidence_id not in ids:
            problems.append("missing_evidence: decoy %s" % row.evidence_id)
    return problems


def check_invariants(dataset):
    """Python twin of truth.invariant_violations."""
    truth = dataset.truth
    scenario = truth.scenario
    problems = []

    retrievable = 0
    unreachable = 0
    for row in truth.decisive:
        if row.availability == "retrievable":
            retrievable = retrievable + 1
        else:
            unreachable = unreachable + 1
    withheld = len(truth.withheld)

    if scenario.expected_outcome == "identifiable":
        if retrievable == 0:
            problems.append("identifiable_without_retrievable_decisive_evidence: -")
        if unreachable > 0:
            problems.append("identifiable_with_unreachable_decisive_evidence: -")
        if withheld > 0:
            problems.append("identifiable_with_withheld_evidence: -")
    else:
        if retrievable > 0:
            problems.append("inconclusive_with_retrievable_decisive_evidence: -")
        if unreachable == 0 and withheld == 0:
            problems.append("inconclusive_without_unreachable_or_withheld_evidence: -")
        if len(truth.alternatives) == 0:
            problems.append("inconclusive_without_alternative_explanation: -")

    decoy_ids = []
    for row in truth.decoys:
        decoy_ids.append(row.evidence_id)
    for row in truth.decisive:
        if row.evidence_id in decoy_ids:
            problems.append("evidence_is_both_decisive_and_decoy: %s" % row.evidence_id)

    identifiable = scenario.expected_outcome == "identifiable"
    if identifiable != scenario.solvable_after_noise:
        problems.append("scenario_inconsistent: outcome vs solvable_after_noise")
    if (not identifiable) != (scenario.unidentifiable_reason is not None):
        problems.append("scenario_inconsistent: outcome vs unidentifiable_reason")
    return problems


def check_tool_rules(dataset):
    identifiable = dataset.truth.scenario.expected_outcome == "identifiable"
    # An identifiable incident may lose a tool for good (an outage of the log store, say),
    # but only a tool none of its decisive evidence depends on: it must stay solvable.
    decisive_tools = []
    for row in dataset.truth.decisive:
        decisive_tools.append(TOOL_FOR_PREFIX[row.evidence_id[0]])
    tool_names = []
    problems = []
    for row in dataset.evidence.tool_behavior:
        tool_names.append(row.tool_name)
        mode = row.rules.get("mode")
        if mode not in FAILURE_MODES:
            problems.append("unknown_failure_mode: %s %s" % (row.tool_name, mode))
            continue
        if identifiable and mode in BLOCKING_MODES and row.tool_name in decisive_tools:
            problems.append("blocking_failure_on_identifiable: %s %s" % (row.tool_name, mode))
        # The MCP server parses rules with the same function; a rule it would refuse must
        # never be loaded.
        try:
            parse_rules([[row.tool_name, row.rules]])
        except ValueError as error:
            first_line = str(error).splitlines()[0]
            problems.append("invalid_failure_rule: %s %s" % (row.tool_name, first_line))
    for name in find_duplicates(tool_names):
        problems.append("duplicate_tool_rule: %s" % name)

    # Unreachable decisive evidence must really be blocked: a persistent rule on its tool.
    persistent_tools = []
    for row in dataset.evidence.tool_behavior:
        if row.rules.get("mode") == "persistent":
            persistent_tools.append(row.tool_name)
    for row in dataset.truth.decisive:
        if row.availability != "unreachable":
            continue
        tool = TOOL_FOR_PREFIX[row.evidence_id[0]]
        if tool not in persistent_tools:
            problems.append("unreachable_without_persistent_rule: %s needs a persistent rule on %s"
                            % (row.evidence_id, tool))
    return problems


def check_log_gaps(dataset):
    problems = []
    for row in dataset.truth.noise:
        if row.noise_type != "log_gap":
            continue
        service = row.params["service"]
        start = datetime.fromisoformat(row.params["start"])
        end = datetime.fromisoformat(row.params["end"])
        for log in dataset.evidence.logs:
            if log.service == service and start <= log.ts < end:
                problems.append("log_inside_gap: %s" % log.evidence_id)
    return problems


def forbidden_tokens(dataset):
    """Lower-case strings that must never appear in agent-visible data."""
    scenario = dataset.truth.scenario
    tokens = [scenario.plan_id.lower(), scenario.archetype.lower(), scenario.mechanism.lower()]
    for word in LEAK_WORDS:
        tokens.append(word)
    return tokens


def find_leaks(dataset):
    strings = []
    collect_strings(dataset.evidence.model_dump(mode="json"), strings)
    tokens = forbidden_tokens(dataset)
    problems = []
    for text in strings:
        lowered = text.lower()
        for token in tokens:
            if token in lowered:
                problems.append("answer_key_leak: %r in %r" % (token, text[:80]))
    return problems


def find_problems(dataset):
    problems = []
    problems.extend(check_ids(dataset))
    problems.extend(check_services(dataset))
    problems.extend(check_times(dataset))
    problems.extend(check_truth_references(dataset))
    problems.extend(check_invariants(dataset))
    problems.extend(check_tool_rules(dataset))
    problems.extend(check_log_gaps(dataset))
    problems.extend(find_leaks(dataset))
    return problems
