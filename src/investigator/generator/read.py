"""Read one incident back from Postgres as an IncidentDataset (evidence and truth).

Needs a role that can read truth: scorer_ro or generator_rw. Every ORDER BY uses the "C"
collation so the order matches Python's string sort whatever the database's locale, which
keeps checksums identical between a build and its loaded copy.
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


def read_evidence(cursor, incident_id):
    cursor.execute(
        "SELECT incident_id, domain, reported_at, window_start, window_end, summary, reporter "
        "FROM evidence.incidents WHERE incident_id = %s",
        (incident_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise KeyError("no incident " + incident_id)
    incident = IncidentRow(incident_id=row[0], domain=row[1], reported_at=row[2],
                           window_start=row[3], window_end=row[4], summary=row[5],
                           reporter=row[6])

    cursor.execute(
        'SELECT service_id, name, role, instance_count FROM evidence.services '
        'WHERE incident_id = %s ORDER BY name COLLATE "C"',
        (incident_id,),
    )
    services = []
    names = {}
    for service_id, name, role, count in cursor.fetchall():
        names[service_id] = name
        services.append(ServiceRow(name=name, role=role, instance_count=count))

    cursor.execute(
        'SELECT evidence_id, caller_id, callee_id, config FROM evidence.service_dependencies '
        'WHERE incident_id = %s ORDER BY evidence_id COLLATE "C"',
        (incident_id,),
    )
    dependencies = []
    for evidence_id, caller_id, callee_id, config in cursor.fetchall():
        dependencies.append(DependencyRow(evidence_id=evidence_id, caller=names[caller_id],
                                          callee=names[callee_id], config=config))

    cursor.execute(
        'SELECT evidence_id, service_id, instance, ts, level, message, attrs '
        'FROM evidence.log_entries WHERE incident_id = %s ORDER BY evidence_id COLLATE "C"',
        (incident_id,),
    )
    logs = []
    for evidence_id, service_id, instance, ts, level, message, attrs in cursor.fetchall():
        logs.append(LogRow(evidence_id=evidence_id, service=names[service_id], instance=instance,
                           ts=ts, level=level, message=message, attrs=attrs))

    cursor.execute(
        'SELECT series_id, ts, value FROM evidence.metric_points WHERE incident_id = %s '
        'ORDER BY series_id COLLATE "C", ts',
        (incident_id,),
    )
    points_by_series = {}
    for series_id, ts, value in cursor.fetchall():
        if series_id not in points_by_series:
            points_by_series[series_id] = []
        points_by_series[series_id].append(MetricPoint(ts=ts, value=value))

    cursor.execute(
        'SELECT evidence_id, service_id, instance, metric_name, unit, source, interval_s '
        'FROM evidence.metric_series WHERE incident_id = %s ORDER BY evidence_id COLLATE "C"',
        (incident_id,),
    )
    metrics = []
    for evidence_id, service_id, instance, name, unit, source, interval_s in cursor.fetchall():
        points = points_by_series.get(evidence_id, [])
        metrics.append(MetricSeriesRow(evidence_id=evidence_id, service=names[service_id],
                                       instance=instance, metric_name=name, unit=unit,
                                       source=source, interval_s=interval_s, points=points))

    cursor.execute(
        """
        SELECT c.evidence_id, c.service_id, c.ts, c.change_type, c.version, c.author, c.summary,
               d.diff
          FROM evidence.changes c
          LEFT JOIN evidence.change_details d ON d.evidence_id = c.evidence_id
         WHERE c.incident_id = %s
         ORDER BY c.evidence_id COLLATE "C"
        """,
        (incident_id,),
    )
    changes = []
    for evidence_id, service_id, ts, change_type, version, author, summary, diff in cursor.fetchall():
        changes.append(ChangeRow(evidence_id=evidence_id, service=names[service_id], ts=ts,
                                 change_type=change_type, version=version, author=author,
                                 summary=summary, diff=diff))

    cursor.execute(
        'SELECT evidence_id, occurred_at, title, root_cause_category, root_cause_component, '
        'resolution, symptoms FROM evidence.past_incidents WHERE incident_id = %s '
        'ORDER BY evidence_id COLLATE "C"',
        (incident_id,),
    )
    past_incidents = []
    for row in cursor.fetchall():
        past_incidents.append(PastIncidentRow(evidence_id=row[0], occurred_at=row[1], title=row[2],
                                              root_cause_category=row[3],
                                              root_cause_component=row[4], resolution=row[5],
                                              symptoms=row[6]))

    cursor.execute(
        'SELECT tool_name, rules FROM evidence.tool_behavior WHERE incident_id = %s '
        'ORDER BY tool_name COLLATE "C"',
        (incident_id,),
    )
    tool_behavior = []
    for tool_name, rules in cursor.fetchall():
        tool_behavior.append(ToolBehaviorRow(tool_name=tool_name, rules=rules))

    return EvidenceSet(incident=incident, services=services, dependencies=dependencies,
                       logs=logs, metrics=metrics, changes=changes,
                       past_incidents=past_incidents, tool_behavior=tool_behavior)


def read_truth(cursor, incident_id):
    cursor.execute(
        """
        SELECT plan_id, archetype, seed, generator_version, expected_outcome,
               solvable_after_noise, unidentifiable_reason, root_component, root_category,
               mechanism, contributing_factors
          FROM truth.scenarios WHERE incident_id = %s
        """,
        (incident_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise KeyError("no scenario for " + incident_id)
    scenario = ScenarioRow(plan_id=row[0], archetype=row[1], seed=row[2],
                           generator_version=row[3], expected_outcome=row[4],
                           solvable_after_noise=row[5], unidentifiable_reason=row[6],
                           root_component=row[7], root_category=row[8], mechanism=row[9],
                           contributing_factors=row[10])

    cursor.execute(
        'SELECT evidence_id, availability FROM truth.decisive_evidence WHERE incident_id = %s '
        'ORDER BY evidence_id COLLATE "C"',
        (incident_id,),
    )
    decisive = []
    for evidence_id, availability in cursor.fetchall():
        decisive.append(DecisiveRow(evidence_id=evidence_id, availability=availability))

    cursor.execute(
        'SELECT evidence_id, decoy_type FROM truth.decoys WHERE incident_id = %s '
        'ORDER BY evidence_id COLLATE "C"',
        (incident_id,),
    )
    decoys = []
    for evidence_id, decoy_type in cursor.fetchall():
        decoys.append(DecoyRow(evidence_id=evidence_id, decoy_type=decoy_type))

    cursor.execute(
        'SELECT kind, description, would_have_shown FROM truth.withheld_evidence '
        'WHERE incident_id = %s ORDER BY withheld_id',
        (incident_id,),
    )
    withheld = []
    for kind, description, would_have_shown in cursor.fetchall():
        withheld.append(WithheldRow(kind=kind, description=description,
                                    would_have_shown=would_have_shown))

    cursor.execute(
        'SELECT category, component, why_plausible FROM truth.alternative_explanations '
        'WHERE incident_id = %s ORDER BY category COLLATE "C", component COLLATE "C"',
        (incident_id,),
    )
    alternatives = []
    for category, component, why_plausible in cursor.fetchall():
        alternatives.append(AlternativeRow(category=category, component=component,
                                           why_plausible=why_plausible))

    cursor.execute(
        'SELECT noise_type, params FROM truth.noise_applied WHERE incident_id = %s '
        'ORDER BY noise_id',
        (incident_id,),
    )
    noise = []
    for noise_type, params in cursor.fetchall():
        noise.append(NoiseRow(noise_type=noise_type, params=params))

    return TruthSet(scenario=scenario, decisive=decisive, decoys=decoys, withheld=withheld,
                    alternatives=alternatives, noise=noise)


def read_dataset(connection, incident_id):
    with connection.transaction():
        cursor = connection.cursor()
        evidence = read_evidence(cursor, incident_id)
        truth = read_truth(cursor, incident_id)
    return IncidentDataset(evidence=evidence, truth=truth)
