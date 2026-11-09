"""Write one IncidentDataset into Postgres. Use a generator_rw connection.

The whole load is one transaction: it removes any earlier build of the same plan entry,
inserts evidence then truth, and asks truth.invariant_violations for a verdict before
committing. Any problem raises GeneratorError and rolls everything back, so a failed
rebuild leaves the previous build untouched.

An incident that already has evaluation runs cannot be rebuilt: eval.runs has no cascade on
purpose, so the delete fails with a foreign-key error.
"""

from psycopg.types.json import Jsonb

from investigator.generator.validate import find_problems


class GeneratorError(Exception):
    pass


def delete_incident_rows(cursor, plan_id, incident_id):
    """Remove an earlier build: by plan ID (any generator version) and by incident ID."""
    cursor.execute(
        """
        DELETE FROM evidence.incidents
         WHERE incident_id = %s
            OR incident_id IN (SELECT incident_id FROM truth.scenarios WHERE plan_id = %s)
        """,
        (incident_id, plan_id),
    )


def delete_plan(connection, plan_id):
    with connection.transaction():
        cursor = connection.cursor()
        cursor.execute(
            """
            DELETE FROM evidence.incidents
             WHERE incident_id IN (SELECT incident_id FROM truth.scenarios WHERE plan_id = %s)
            """,
            (plan_id,),
        )


def insert_evidence(cursor, evidence):
    incident = evidence.incident
    incident_id = incident.incident_id

    cursor.execute(
        """
        INSERT INTO evidence.incidents
          (incident_id, domain, reported_at, window_start, window_end, summary, reporter)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (incident_id, incident.domain, incident.reported_at, incident.window_start,
         incident.window_end, incident.summary, incident.reporter),
    )

    service_ids = {}
    for service in evidence.services:
        cursor.execute(
            """
            INSERT INTO evidence.services (incident_id, name, role, instance_count)
            VALUES (%s, %s, %s, %s) RETURNING service_id
            """,
            (incident_id, service.name, service.role, service.instance_count),
        )
        service_ids[service.name] = cursor.fetchone()[0]

    index_rows = []
    for item in evidence.dependencies:
        index_rows.append((item.evidence_id, incident_id, "dependency"))
    for item in evidence.logs:
        index_rows.append((item.evidence_id, incident_id, "log"))
    for item in evidence.metrics:
        index_rows.append((item.evidence_id, incident_id, "metric"))
    for item in evidence.changes:
        index_rows.append((item.evidence_id, incident_id, "change"))
    for item in evidence.past_incidents:
        index_rows.append((item.evidence_id, incident_id, "past_incident"))
    cursor.executemany(
        "INSERT INTO evidence.evidence_index (evidence_id, incident_id, kind) VALUES (%s, %s, %s)",
        index_rows,
    )

    rows = []
    for item in evidence.dependencies:
        rows.append((item.evidence_id, incident_id, service_ids[item.caller],
                     service_ids[item.callee], Jsonb(item.config)))
    cursor.executemany(
        """
        INSERT INTO evidence.service_dependencies
          (evidence_id, incident_id, caller_id, callee_id, config)
        VALUES (%s, %s, %s, %s, %s)
        """,
        rows,
    )

    rows = []
    for item in evidence.logs:
        rows.append((item.evidence_id, incident_id, service_ids[item.service], item.instance,
                     item.ts, item.level, item.message, Jsonb(item.attrs)))
    cursor.executemany(
        """
        INSERT INTO evidence.log_entries
          (evidence_id, incident_id, service_id, instance, ts, level, message, attrs)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        rows,
    )

    series_rows = []
    point_rows = []
    for item in evidence.metrics:
        series_rows.append((item.evidence_id, incident_id, service_ids[item.service],
                            item.instance, item.metric_name, item.unit, item.source,
                            item.interval_s))
        for point in item.points:
            point_rows.append((incident_id, item.evidence_id, point.ts, point.value))
    cursor.executemany(
        """
        INSERT INTO evidence.metric_series
          (evidence_id, incident_id, service_id, instance, metric_name, unit, source, interval_s)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        series_rows,
    )
    cursor.executemany(
        "INSERT INTO evidence.metric_points (incident_id, series_id, ts, value) "
        "VALUES (%s, %s, %s, %s)",
        point_rows,
    )

    change_rows = []
    detail_rows = []
    for item in evidence.changes:
        change_rows.append((item.evidence_id, incident_id, service_ids[item.service], item.ts,
                            item.change_type, item.version, item.author, item.summary))
        if item.diff is not None:
            detail_rows.append((item.evidence_id, incident_id, Jsonb(item.diff)))
    cursor.executemany(
        """
        INSERT INTO evidence.changes
          (evidence_id, incident_id, service_id, ts, change_type, version, author, summary)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        change_rows,
    )
    cursor.executemany(
        "INSERT INTO evidence.change_details (evidence_id, incident_id, diff) VALUES (%s, %s, %s)",
        detail_rows,
    )

    rows = []
    for item in evidence.past_incidents:
        rows.append((item.evidence_id, incident_id, item.occurred_at, item.title,
                     item.root_cause_category, item.root_cause_component, item.resolution,
                     Jsonb(item.symptoms)))
    cursor.executemany(
        """
        INSERT INTO evidence.past_incidents
          (evidence_id, incident_id, occurred_at, title, root_cause_category,
           root_cause_component, resolution, symptoms)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        rows,
    )

    rows = []
    for item in evidence.tool_behavior:
        rows.append((incident_id, item.tool_name, Jsonb(item.rules)))
    cursor.executemany(
        "INSERT INTO evidence.tool_behavior (incident_id, tool_name, rules) VALUES (%s, %s, %s)",
        rows,
    )


def insert_truth(cursor, incident_id, truth):
    scenario = truth.scenario
    cursor.execute(
        """
        INSERT INTO truth.scenarios
          (incident_id, plan_id, archetype, seed, generator_version, expected_outcome,
           solvable_after_noise, unidentifiable_reason, root_component, root_category,
           mechanism, contributing_factors)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (incident_id, scenario.plan_id, scenario.archetype, scenario.seed,
         scenario.generator_version, scenario.expected_outcome, scenario.solvable_after_noise,
         scenario.unidentifiable_reason, scenario.root_component, scenario.root_category,
         scenario.mechanism, Jsonb(scenario.contributing_factors)),
    )

    rows = []
    for row in truth.decisive:
        rows.append((incident_id, row.evidence_id, row.availability))
    cursor.executemany(
        "INSERT INTO truth.decisive_evidence (incident_id, evidence_id, availability) "
        "VALUES (%s, %s, %s)",
        rows,
    )

    rows = []
    for row in truth.decoys:
        rows.append((incident_id, row.evidence_id, row.decoy_type))
    cursor.executemany(
        "INSERT INTO truth.decoys (incident_id, evidence_id, decoy_type) VALUES (%s, %s, %s)",
        rows,
    )

    # Withheld and noise rows keep their order through their serial IDs, so insert one by one.
    for row in truth.withheld:
        would_have_shown = None
        if row.would_have_shown is not None:
            would_have_shown = Jsonb(row.would_have_shown)
        cursor.execute(
            "INSERT INTO truth.withheld_evidence (incident_id, kind, description, would_have_shown) "
            "VALUES (%s, %s, %s, %s)",
            (incident_id, row.kind, row.description, would_have_shown),
        )

    rows = []
    for row in truth.alternatives:
        rows.append((incident_id, row.category, row.component, row.why_plausible))
    cursor.executemany(
        "INSERT INTO truth.alternative_explanations (incident_id, category, component, why_plausible) "
        "VALUES (%s, %s, %s, %s)",
        rows,
    )

    for row in truth.noise:
        cursor.execute(
            "INSERT INTO truth.noise_applied (incident_id, noise_type, params) VALUES (%s, %s, %s)",
            (incident_id, row.noise_type, Jsonb(row.params)),
        )


def load_incident(connection, dataset):
    problems = find_problems(dataset)
    if problems:
        raise GeneratorError("dataset failed validation:\n" + "\n".join(problems))

    incident_id = dataset.evidence.incident.incident_id
    with connection.transaction():
        cursor = connection.cursor()
        delete_incident_rows(cursor, dataset.truth.scenario.plan_id, incident_id)
        insert_evidence(cursor, dataset.evidence)
        insert_truth(cursor, incident_id, dataset.truth)

        cursor.execute(
            "SELECT violation FROM truth.invariant_violations WHERE incident_id = %s",
            (incident_id,),
        )
        violations = []
        for row in cursor.fetchall():
            violations.append(row[0])
        if violations:
            # Raising inside the transaction block rolls the whole load back.
            raise GeneratorError("truth.invariant_violations: " + ", ".join(violations))
