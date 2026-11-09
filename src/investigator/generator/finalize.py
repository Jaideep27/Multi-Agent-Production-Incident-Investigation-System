"""Turn a finished draft into an IncidentDataset: assign evidence IDs, build the Pydantic rows.

IDs are numbered per kind after sorting by time (then by stable text keys), so the number in
an ID reflects when something happened, never whether it matters.
"""

from investigator.generator.dataset import (
    AlternativeRow,
    ChangeRow,
    DecisiveRow,
    DecoyRow,
    DependencyRow,
    EvidenceSet,
    IncidentDataset,
    IncidentRow,
    LogRow,
    MetricPoint,
    MetricSeriesRow,
    NoiseRow,
    PastIncidentRow,
    ScenarioRow,
    ServiceRow,
    ToolBehaviorRow,
    TruthSet,
    WithheldRow,
)
from investigator.models.common import EVIDENCE_PREFIXES

ID_DIGITS = 6


def edge_key(edge):
    return (edge.caller, edge.callee)


def log_key(log):
    instance = log.instance or ""
    return (log.ts, log.service, instance, log.level, log.message)


def metric_key(metric):
    instance = metric.instance or ""
    return (metric.service, metric.metric_name, instance, metric.source)


def change_key(change):
    return (change.ts, change.service, change.summary)


def past_key(past):
    return (past.occurred_at, past.title)


def number_items(items, kind, incident_hash):
    """Give each item its ID, in the order given, and return that order."""
    prefix = EVIDENCE_PREFIXES[kind]
    number = 1
    for item in items:
        item.evidence_id = "%s-%s-%0*d" % (prefix, incident_hash, ID_DIGITS, number)
        number = number + 1
    return items


def evidence_id_key(row):
    return row.evidence_id


def alternative_key(row):
    return (row.category, row.component)


def build_evidence(draft, incident_id, edges, logs, metrics, changes, past_incidents):
    services = []
    for name, role, count in sorted(draft.services):
        services.append(ServiceRow(name=name, role=role, instance_count=count))

    dependency_rows = []
    for edge in edges:
        dependency_rows.append(DependencyRow(evidence_id=edge.evidence_id, caller=edge.caller,
                                             callee=edge.callee, config=edge.config))

    log_rows = []
    for log in logs:
        log_rows.append(LogRow(evidence_id=log.evidence_id, service=log.service,
                               instance=log.instance, ts=log.ts, level=log.level,
                               message=log.message, attrs=log.attrs))

    metric_rows = []
    for metric in metrics:
        points = []
        for ts, value in metric.points:
            points.append(MetricPoint(ts=ts, value=value))
        metric_rows.append(MetricSeriesRow(evidence_id=metric.evidence_id, service=metric.service,
                                           instance=metric.instance, metric_name=metric.metric_name,
                                           unit=metric.unit, source=metric.source,
                                           interval_s=metric.interval_s, points=points))

    change_rows = []
    for change in changes:
        change_rows.append(ChangeRow(evidence_id=change.evidence_id, service=change.service,
                                     ts=change.ts, change_type=change.change_type,
                                     version=change.version, author=change.author,
                                     summary=change.summary, diff=change.diff))

    past_rows = []
    for past in past_incidents:
        past_rows.append(PastIncidentRow(evidence_id=past.evidence_id,
                                         occurred_at=past.occurred_at, title=past.title,
                                         root_cause_category=past.category,
                                         root_cause_component=past.component,
                                         resolution=past.resolution, symptoms=past.symptoms))

    tool_rows = []
    for tool_name, rules in sorted(draft.tool_rules):
        tool_rows.append(ToolBehaviorRow(tool_name=tool_name, rules=rules))

    incident = IncidentRow(incident_id=incident_id, domain=draft.domain,
                           reported_at=draft.reported_at, window_start=draft.window_start,
                           window_end=draft.window_end, summary=draft.summary,
                           reporter=draft.reporter)

    return EvidenceSet(incident=incident, services=services, dependencies=dependency_rows,
                       logs=log_rows, metrics=metric_rows, changes=change_rows,
                       past_incidents=past_rows, tool_behavior=tool_rows)


def build_truth(draft, scenario):
    decisive = []
    for item, availability in draft.decisive:
        decisive.append(DecisiveRow(evidence_id=item.evidence_id, availability=availability))
    decisive.sort(key=evidence_id_key)

    decoys = []
    for item, decoy_type in draft.decoys:
        decoys.append(DecoyRow(evidence_id=item.evidence_id, decoy_type=decoy_type))
    decoys.sort(key=evidence_id_key)

    withheld = []
    for row in draft.withheld:
        withheld.append(WithheldRow(kind=row["kind"], description=row["description"],
                                    would_have_shown=row["would_have_shown"]))

    alternatives = []
    for row in draft.alternatives:
        alternatives.append(AlternativeRow(category=row["category"], component=row["component"],
                                           why_plausible=row["why_plausible"]))
    alternatives.sort(key=alternative_key)

    noise = []
    for noise_type, params in draft.noise:
        noise.append(NoiseRow(noise_type=noise_type, params=params))

    return TruthSet(scenario=scenario, decisive=decisive, decoys=decoys, withheld=withheld,
                    alternatives=alternatives, noise=noise)


def finalize(draft, incident_id, scenario):
    """scenario is a ready ScenarioRow; incident_id is inc_<8 hex>."""
    incident_hash = incident_id[len("inc_"):]
    edges = number_items(sorted(draft.edges, key=edge_key), "dependency", incident_hash)
    logs = number_items(sorted(draft.logs, key=log_key), "log", incident_hash)
    metrics = number_items(sorted(draft.metrics, key=metric_key), "metric", incident_hash)
    changes = number_items(sorted(draft.changes, key=change_key), "change", incident_hash)
    past_incidents = number_items(sorted(draft.past_incidents, key=past_key), "past_incident",
                                  incident_hash)

    evidence = build_evidence(draft, incident_id, edges, logs, metrics, changes, past_incidents)
    truth = build_truth(draft, scenario)
    return IncidentDataset(evidence=evidence, truth=truth)
