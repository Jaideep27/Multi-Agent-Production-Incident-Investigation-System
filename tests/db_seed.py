"""Minimal seed data for the database tests.

Every evidence table, every truth table and every eval table gets at least one row for
each seeded incident, so a test that says "this role sees nothing" cannot pass just
because the tables were empty.

The code is deliberately plain: explicit variables and simple loops.
"""

from psycopg.types.json import Jsonb

INCIDENT_A = "inc_aaaaaaaa"   # identifiable
INCIDENT_B = "inc_bbbbbbbb"   # inconclusive by design

EVIDENCE_TABLES_WITH_ROWS = [
    "incidents",
    "services",
    "service_dependencies",
    "evidence_index",
    "log_entries",
    "metric_series",
    "metric_points",
    "changes",
    "change_details",
    "past_incidents",
    "tool_behavior",
]


def seed_incident(cursor, incident_id, tag, outcome, with_run=True):
    """Insert one complete incident. outcome is 'identifiable' or 'inconclusive'."""

    # ---- incident and services --------------------------------------------------
    cursor.execute(
        """
        INSERT INTO evidence.incidents
          (incident_id, domain, reported_at, window_start, window_end, summary, reporter)
        VALUES (%s, 'payment_gateway', '2026-11-07T14:12:00Z', '2026-11-07T13:30:00Z',
                '2026-11-07T14:30:00Z', 'UPI success rate dropped', 'support-oncall')
        """,
        (incident_id,),
    )

    service_ids = {}
    service_specs = [
        ("payment-router", "app"),
        ("postgres", "database"),
        ("bank-adapter", "app"),
    ]
    for name, role in service_specs:
        cursor.execute(
            """
            INSERT INTO evidence.services (incident_id, name, role, instance_count)
            VALUES (%s, %s, %s, 2) RETURNING service_id
            """,
            (incident_id, name, role),
        )
        service_ids[name] = cursor.fetchone()[0]

    # ---- evidence index (one row per evidence item) -----------------------------
    log_ids = ["L-%s-1" % tag, "L-%s-2" % tag, "L-%s-3" % tag]
    metric_ids = ["M-%s-1" % tag, "M-%s-2" % tag]
    change_ids = ["C-%s-1" % tag, "C-%s-2" % tag]
    past_ids = ["P-%s-1" % tag]
    dependency_ids = ["D-%s-1" % tag]

    index_rows = []
    for evidence_id in dependency_ids:
        index_rows.append((evidence_id, "dependency"))
    for evidence_id in log_ids:
        index_rows.append((evidence_id, "log"))
    for evidence_id in metric_ids:
        index_rows.append((evidence_id, "metric"))
    for evidence_id in change_ids:
        index_rows.append((evidence_id, "change"))
    for evidence_id in past_ids:
        index_rows.append((evidence_id, "past_incident"))

    for evidence_id, kind in index_rows:
        cursor.execute(
            "INSERT INTO evidence.evidence_index (evidence_id, incident_id, kind) "
            "VALUES (%s, %s, %s)",
            (evidence_id, incident_id, kind),
        )

    # ---- dependencies -----------------------------------------------------------
    cursor.execute(
        """
        INSERT INTO evidence.service_dependencies
          (evidence_id, incident_id, caller_id, callee_id, config)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            dependency_ids[0],
            incident_id,
            service_ids["payment-router"],
            service_ids["postgres"],
            Jsonb({"timeout_ms": 3000, "max_pool": 20}),
        ),
    )

    # ---- logs -------------------------------------------------------------------
    log_specs = [
        (log_ids[0], "payment-router", "ERROR", "connection pool exhausted, waited 5000ms",
         "2026-11-07T14:06:11Z"),
        (log_ids[1], "bank-adapter", "WARN", "upstream timeout after 3000ms",
         "2026-11-07T14:04:02Z"),
        (log_ids[2], "payment-router", "INFO", "request completed",
         "2026-11-07T13:58:40Z"),
    ]
    for evidence_id, service_name, level, message, ts in log_specs:
        cursor.execute(
            """
            INSERT INTO evidence.log_entries
              (evidence_id, incident_id, service_id, instance, ts, level, message, attrs)
            VALUES (%s, %s, %s, 'pod-1', %s, %s, %s, %s)
            """,
            (evidence_id, incident_id, service_ids[service_name], ts, level, message,
             Jsonb({"status": 503})),
        )

    # ---- metrics ----------------------------------------------------------------
    metric_specs = [
        (metric_ids[0], "payment-router", "cpu_pct", "percent", "prometheus"),
        (metric_ids[1], "bank-adapter", "p99_latency_ms", "ms", "prometheus"),
    ]
    for evidence_id, service_name, metric_name, unit, source in metric_specs:
        cursor.execute(
            """
            INSERT INTO evidence.metric_series
              (evidence_id, incident_id, service_id, instance, metric_name, unit, source,
               interval_s)
            VALUES (%s, %s, %s, NULL, %s, %s, %s, 60)
            """,
            (evidence_id, incident_id, service_ids[service_name], metric_name, unit, source),
        )
        for minute in range(5):
            cursor.execute(
                """
                INSERT INTO evidence.metric_points (incident_id, series_id, ts, value)
                VALUES (%s, %s, '2026-11-07T14:00:00Z'::timestamptz + make_interval(mins => %s), %s)
                """,
                (incident_id, evidence_id, minute, 40.0 + minute),
            )

    # ---- changes ----------------------------------------------------------------
    cursor.execute(
        """
        INSERT INTO evidence.changes
          (evidence_id, incident_id, service_id, ts, change_type, version, author, summary)
        VALUES (%s, %s, %s, '2026-11-07T14:02:00Z', 'config_change', NULL, 'svc-infra',
                'payment-router configuration updated')
        """,
        (change_ids[0], incident_id, service_ids["payment-router"]),
    )
    cursor.execute(
        """
        INSERT INTO evidence.changes
          (evidence_id, incident_id, service_id, ts, change_type, version, author, summary)
        VALUES (%s, %s, %s, '2026-11-07T13:55:00Z', 'deploy', 'v4.2.1', 'team-comms',
                'bank-adapter deploy v4.2.1')
        """,
        (change_ids[1], incident_id, service_ids["bank-adapter"]),
    )
    cursor.execute(
        "INSERT INTO evidence.change_details (evidence_id, incident_id, diff) "
        "VALUES (%s, %s, %s)",
        (change_ids[0], incident_id, Jsonb({"max_pool": {"from": 100, "to": 20}})),
    )

    # ---- past incident and tool behaviour ---------------------------------------
    cursor.execute(
        """
        INSERT INTO evidence.past_incidents
          (evidence_id, incident_id, occurred_at, title, root_cause_category,
           root_cause_component, resolution, symptoms)
        VALUES (%s, %s, '2026-08-01T10:00:00Z', 'UPI failures during bank outage',
                'dependency_failure', 'bank-adapter', 'failed over to secondary bank',
                %s)
        """,
        (past_ids[0], incident_id, Jsonb(["success rate drop", "bank-adapter latency up"])),
    )
    cursor.execute(
        "INSERT INTO evidence.tool_behavior (incident_id, tool_name, rules) VALUES (%s, %s, %s)",
        (incident_id, "query_logs", Jsonb({"mode": "transient", "fail_first_n": 1})),
    )
    cursor.execute(
        "INSERT INTO evidence.tool_behavior (incident_id, tool_name, rules) VALUES (%s, %s, %s)",
        (incident_id, "get_change_history", Jsonb({"mode": "partial", "keep_fraction": 0.5})),
    )

    # ---- truth ------------------------------------------------------------------
    solvable = outcome == "identifiable"
    reason = None
    if not solvable:
        reason = "the change record and lock-wait logs are unreachable"

    cursor.execute(
        """
        INSERT INTO truth.scenarios
          (incident_id, plan_id, archetype, seed, generator_version, expected_outcome,
           solvable_after_noise, unidentifiable_reason, root_component, root_category,
           mechanism, contributing_factors)
        VALUES (%s, %s, 'pool_exhaustion', 1, 'test', %s, %s, %s, 'payment-router',
                'config_change', 'db connection pool reduced from 100 to 20', %s)
        """,
        (incident_id, "SEED-" + tag.upper(), outcome, solvable, reason, Jsonb([])),
    )

    if solvable:
        cursor.execute(
            "INSERT INTO truth.decisive_evidence (incident_id, evidence_id, availability) "
            "VALUES (%s, %s, 'retrievable')",
            (incident_id, change_ids[0]),
        )
        cursor.execute(
            "INSERT INTO truth.decisive_evidence (incident_id, evidence_id, availability) "
            "VALUES (%s, %s, 'retrievable')",
            (incident_id, log_ids[0]),
        )
    else:
        cursor.execute(
            "INSERT INTO truth.decisive_evidence (incident_id, evidence_id, availability) "
            "VALUES (%s, %s, 'unreachable')",
            (incident_id, change_ids[0]),
        )
        cursor.execute(
            "INSERT INTO truth.withheld_evidence (incident_id, kind, description, would_have_shown) "
            "VALUES (%s, 'log', 'lock-wait logs', %s)",
            (incident_id, Jsonb({"pattern": "pool exhausted"})),
        )
        cursor.execute(
            "INSERT INTO truth.alternative_explanations "
            "(incident_id, category, component, why_plausible) "
            "VALUES (%s, 'dependency_failure', 'bank-adapter', 'looks like a bank outage')",
            (incident_id,),
        )

    cursor.execute(
        "INSERT INTO truth.decoys (incident_id, evidence_id, decoy_type) "
        "VALUES (%s, %s, 'red_herring_change')",
        (incident_id, change_ids[1]),
    )
    cursor.execute(
        "INSERT INTO truth.decoys (incident_id, evidence_id, decoy_type) "
        "VALUES (%s, %s, 'lookalike_history')",
        (incident_id, past_ids[0]),
    )
    cursor.execute(
        "INSERT INTO truth.noise_applied (incident_id, noise_type, params) VALUES (%s, %s, %s)",
        (incident_id, "log_gap", Jsonb({"from": "14:10", "to": "14:15"})),
    )

    # ---- eval -------------------------------------------------------------------
    if with_run:
        cursor.execute(
            """
            INSERT INTO eval.runs (incident_id, system, model, status, rounds)
            VALUES (%s, 'multi_agent', 'haiku', 'completed', 2) RETURNING run_id
            """,
            (incident_id,),
        )
        run_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO eval.llm_calls (run_id, agent, status, input_tokens, output_tokens, "
            "latency_ms) VALUES (%s, 'orchestrator', 'ok', 1200, 300, 2500)",
            (run_id,),
        )
        cursor.execute(
            "INSERT INTO eval.tool_calls (run_id, tool, args, status, latency_ms, evidence_ids) "
            "VALUES (%s, 'query_logs', %s, 'ok', 40, %s)",
            (run_id, Jsonb({"service": "payment-router"}), [log_ids[0]]),
        )
        cursor.execute(
            """
            INSERT INTO eval.scores
              (run_id, root_cause_correct, outcome_correct, grounding_cited, grounding_existing,
               decoy_hit, confidence, tool_call_count, llm_call_count, input_tokens,
               output_tokens, latency_ms, rounds)
            VALUES (%s, true, true, 3, 3, false, 0.8, 1, 1, 1200, 300, 2500, 2)
            """,
            (run_id,),
        )


def seed_all(cursor):
    seed_incident(cursor, INCIDENT_A, "a", "identifiable")
    seed_incident(cursor, INCIDENT_B, "b", "inconclusive")
