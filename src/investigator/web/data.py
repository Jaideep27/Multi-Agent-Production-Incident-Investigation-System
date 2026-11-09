"""Read-only data for the viewer: finished runs, their call logs, blackboards and evidence.

Uses the scorer_ro role (docs/implementation_plan.md §9: the API may reveal the answer key
after a run). It never writes and never starts a run, so it costs no model quota. The answer
key is only returned once every run shown for the incident has finished.
"""

import json
from pathlib import Path

import psycopg

from investigator import db
from investigator.company_context import load_company_contexts
from investigator.scorer.report import SYSTEMS, summarise
from investigator.web.evidence import (describe_evidence, empty_conditions, key_evidence_of_run,
                                       relative_shift)
from investigator.web.exchanges import recover_from_cache, shape_exchanges
from investigator.web.rows import epoch_ms, rows_as_dicts, top_hypothesis
from investigator.web.run_views import (all_finished, newest_per_system, read_blackboard,
                                        read_conversation, run_limits, split_into_steps,
                                        tool_calls_by_round)
from investigator.web.story import build_story, load_stories
from investigator.web.tool_views import tool_overview

BACKENDS = ["ClaudeCliBackend"]


class DataStore:
    def __init__(self, label, runs_dir="runs", conninfo=None, cache_dir=".llm_cache"):
        self.label = label
        self.runs_dir = Path(runs_dir)
        self.cache_dir = Path(cache_dir)
        self.conninfo = conninfo or db.conninfo_for("scorer_ro")

    def connect(self):
        return psycopg.connect(self.conninfo, autocommit=True)

    # ---------------------------------------------------------- overview

    def evaluation(self):
        connection = self.connect()
        try:
            rows = rows_as_dicts(connection.execute(
                """
                SELECT DISTINCT ON (r.incident_id, r.system)
                       r.incident_id, r.run_id::text AS run_id, r.system, r.status, r.config,
                       r.stats, r.final_report, t.plan_id, t.root_category AS category,
                       t.root_component, t.expected_outcome, i.domain, i.summary,
                       s.root_cause_correct, s.outcome_correct, s.grounding_cited,
                       s.grounding_existing, s.decisive_recall, s.decoy_hit,
                       s.decoys_cited_as_support, s.confidence, s.tool_call_count,
                       s.llm_call_count, s.input_tokens, s.output_tokens, s.latency_ms,
                       (SELECT count(*) FROM eval.tool_calls c
                         WHERE c.run_id = r.run_id) AS tool_attempts
                  FROM eval.runs r
                  JOIN eval.scores s ON s.run_id = r.run_id
                  JOIN truth.scenarios t ON t.incident_id = r.incident_id
                  JOIN evidence.incidents i ON i.incident_id = r.incident_id
                 WHERE r.config->>'label' = %s AND r.config->>'backend' = ANY(%s)
                 ORDER BY r.incident_id, r.system, r.started_at DESC
                """, (self.label, BACKENDS)))
            conditions = self.conditions_for_all(connection)
            # the newest scored result of each case and system, reruns included (for the list)
            newest = rows_as_dicts(connection.execute(
                """
                SELECT DISTINCT ON (r.incident_id, r.system)
                       r.incident_id, r.system, r.config->>'label' AS label,
                       s.root_cause_correct AS correct
                  FROM eval.runs r JOIN eval.scores s ON s.run_id = r.run_id
                 WHERE (r.config->>'label' = %s OR r.config->>'label' LIKE %s)
                   AND r.config->>'backend' = ANY(%s) AND r.final_report IS NOT NULL
                 ORDER BY r.incident_id, r.system, r.started_at DESC
                """, (self.label, self.label + "-rerun-%", BACKENDS)))
        finally:
            connection.close()

        config = {}
        if rows:
            config = run_limits(rows[0]["config"])

        incidents = {}
        order = []
        for row in rows:
            incident_id = row["incident_id"]
            if incident_id not in incidents:
                order.append(incident_id)
                incidents[incident_id] = {
                    "incident_id": incident_id, "plan_id": row["plan_id"],
                    "domain": row["domain"], "summary": row["summary"],
                    "category": row["category"], "root_component": row["root_component"],
                    "expected_outcome": row["expected_outcome"],
                    "conditions": conditions.get(incident_id, empty_conditions()),
                    "runs": {}}
            top = top_hypothesis(row["final_report"])
            incidents[incident_id]["runs"][row["system"]] = {
                "run_id": row["run_id"], "status": row["status"],
                "correct": row["root_cause_correct"], "outcome_correct": row["outcome_correct"],
                "confidence": row["confidence"], "decoy_hit": row["decoy_hit"],
                "outcome": (row["final_report"] or {}).get("outcome"),
                "top_component": top["component"] if top else None,
                "tool_calls": row["tool_call_count"], "llm_calls": row["llm_call_count"],
                "input_tokens": row["input_tokens"], "latency_s": row["latency_ms"] / 1000.0}

        for row in newest:
            if row["incident_id"] in incidents:
                incidents[row["incident_id"]].setdefault("newest", {})[row["system"]] = {
                    "correct": row["correct"], "rerun": row["label"] != self.label}

        listed = []
        for incident_id in order:
            listed.append(incidents[incident_id])
        listed.sort(key=lambda item: item["plan_id"])
        return {"label": self.label, "systems": SYSTEMS, "limits": config,
                "summary": summarise(rows), "incidents": listed}

    def conditions_for_all(self, connection):
        found = {}
        for row in connection.execute(
                "SELECT incident_id, count(*) FROM truth.decoys GROUP BY incident_id"):
            found.setdefault(row[0], empty_conditions())["decoys"] = row[1]
        for row in connection.execute(
                "SELECT incident_id, noise_type FROM truth.noise_applied ORDER BY noise_id"):
            entry = found.setdefault(row[0], empty_conditions())
            if row[1] not in entry["noise"]:
                entry["noise"].append(row[1])
        for row in connection.execute(
                "SELECT incident_id, tool_name FROM evidence.tool_behavior "
                "ORDER BY incident_id, tool_name"):
            found.setdefault(row[0], empty_conditions())["tool_failures"].append(row[1])
        return found

    # ---------------------------------------------------------- one incident

    def run_labels(self, connection, incident_id):
        """The official run and any reruns of this case, oldest first."""
        rows = rows_as_dicts(connection.execute(
            """
            SELECT config->>'label' AS label, min(started_at) AS started_at,
                   min(model) AS model, min(config->>'pattern') AS pattern
              FROM eval.runs
             WHERE incident_id = %s AND final_report IS NOT NULL
               AND (config->>'label' = %s OR config->>'label' LIKE %s)
             GROUP BY config->>'label'
             ORDER BY min(started_at)
            """, (incident_id, self.label, self.label + "-rerun-%")))
        for row in rows:
            row["started_at"] = epoch_ms(row["started_at"])
            row["official"] = row["label"] == self.label
        return rows

    def live_progress(self, label):
        """Runs of one (re)run label, including those still running."""
        connection = self.connect()
        try:
            rows = rows_as_dicts(connection.execute(
                """
                SELECT r.run_id::text AS run_id, r.system, r.status,
                       extract(epoch FROM (coalesce(r.ended_at, now()) - r.started_at))::int AS elapsed_s,
                       (SELECT count(*) FROM eval.llm_calls l WHERE l.run_id = r.run_id) AS llm_calls,
                       (SELECT count(*) FROM eval.tool_calls t WHERE t.run_id = r.run_id) AS tool_calls,
                       s.root_cause_correct AS correct
                  FROM eval.runs r LEFT JOIN eval.scores s ON s.run_id = r.run_id
                 WHERE r.config->>'label' = %s
                 ORDER BY r.started_at
                """, (label,)))
        finally:
            connection.close()
        return rows

    def incident(self, incident_id, run_label=None):
        connection = self.connect()
        try:
            incident_rows = rows_as_dicts(connection.execute(
                "SELECT incident_id, domain, summary, reporter, reported_at, window_start, "
                "window_end FROM evidence.incidents WHERE incident_id = %s", (incident_id,)))
            if not incident_rows:
                return None
            incident = incident_rows[0]
            # The plan ID is only a display label (for example the row name in the results).
            incident["plan_id"] = None
            for row in connection.execute(
                    "SELECT plan_id FROM truth.scenarios WHERE incident_id = %s", (incident_id,)):
                incident["plan_id"] = row[0]
            incident["reported_at"] = epoch_ms(incident["reported_at"])
            incident["window_start"] = epoch_ms(incident["window_start"])
            incident["window_end"] = epoch_ms(incident["window_end"])
            incident["services"] = rows_as_dicts(connection.execute(
                "SELECT name, role, instance_count FROM evidence.services "
                "WHERE incident_id = %s ORDER BY name COLLATE \"C\"", (incident_id,)))
            incident["dependencies"] = rows_as_dicts(connection.execute(
                """
                SELECT a.name AS caller, b.name AS callee, d.config
                  FROM evidence.service_dependencies d
                  JOIN evidence.services a ON a.service_id = d.caller_id
                  JOIN evidence.services b ON b.service_id = d.callee_id
                 WHERE d.incident_id = %s
                 ORDER BY a.name COLLATE "C", b.name COLLATE "C"
                """, (incident_id,)))
            series = rows_as_dicts(connection.execute(
                """
                SELECT m.evidence_id, s.name AS service, m.instance, m.metric_name, m.unit
                  FROM evidence.metric_series m
                  JOIN evidence.services s ON s.service_id = m.service_id
                 WHERE m.incident_id = %s
                 ORDER BY s.name COLLATE "C", m.metric_name COLLATE "C", m.instance NULLS FIRST
                """, (incident_id,)))
            shifts = self.series_shifts(connection, incident_id)
            for item in series:
                item["shift"] = shifts.get(item["evidence_id"], 0.0)
            changes = rows_as_dicts(connection.execute(
                """
                SELECT c.evidence_id, c.ts, s.name AS service, c.change_type, c.version,
                       c.summary
                  FROM evidence.changes c
                  JOIN evidence.services s ON s.service_id = c.service_id
                 WHERE c.incident_id = %s ORDER BY c.ts
                """, (incident_id,)))
            for change in changes:
                change["ts"] = epoch_ms(change["ts"])
            available = self.run_labels(connection, incident_id)
            if run_label:
                # one chosen run (a chip at the top of the page)
                labels = [run_label]
            else:
                # by default the newest result of each system, reruns included
                labels = []
                for option in reversed(available):
                    labels.append(option["label"])
                if not labels:
                    labels = [self.label]

            def runs_for_label(one_label):
                return self.runs_for(connection, incident_id, one_label)

            runs = newest_per_system(labels, runs_for_label)
            label = run_label
            # The test conditions (decoys, noise, injected tool failures) come from the
            # answer-key side, so they are shown only once the runs have finished.
            conditions = None
            truth = None
            if runs and all_finished(runs):
                conditions = self.conditions(connection, incident_id)
                truth = self.truth(connection, incident_id)
                for system in runs:
                    runs[system]["key_evidence"] = key_evidence_of_run(
                        runs[system]["report"], truth["decisive_described"])
            tools = tool_overview(connection, runs, conditions)
            story = build_story(connection, incident, truth, load_stories(),
                                load_company_contexts())
        finally:
            connection.close()
        return {"incident": incident, "series": series, "changes": changes,
                "conditions": conditions, "runs": runs, "truth": truth, "story": story,
                "label": label, "available_runs": available, "tools": tools}

    def series_shifts(self, connection, incident_id):
        values_by_series = {}
        for row in connection.execute(
                "SELECT series_id, value FROM evidence.metric_points WHERE incident_id = %s "
                "ORDER BY series_id, ts", (incident_id,)):
            values_by_series.setdefault(row[0], []).append(row[1])
        shifts = {}
        for series_id in values_by_series:
            shifts[series_id] = relative_shift(values_by_series[series_id])
        return shifts

    def conditions(self, connection, incident_id):
        decoys = rows_as_dicts(connection.execute(
            "SELECT evidence_id, decoy_type FROM truth.decoys WHERE incident_id = %s "
            "ORDER BY evidence_id", (incident_id,)))
        noise = rows_as_dicts(connection.execute(
            "SELECT noise_type, params FROM truth.noise_applied WHERE incident_id = %s "
            "ORDER BY noise_id", (incident_id,)))
        tools = rows_as_dicts(connection.execute(
            "SELECT tool_name, rules FROM evidence.tool_behavior WHERE incident_id = %s "
            "ORDER BY tool_name", (incident_id,)))
        # Which decoys exist is part of the answer key, so only their count is given here.
        return {"decoys": len(decoys), "noise": noise, "tool_failures": tools}

    def truth(self, connection, incident_id):
        scenario = rows_as_dicts(connection.execute(
            "SELECT root_component, root_category, mechanism, expected_outcome, "
            "unidentifiable_reason FROM truth.scenarios WHERE incident_id = %s",
            (incident_id,)))[0]
        decisive = []
        for row in connection.execute(
                "SELECT evidence_id FROM truth.decisive_evidence WHERE incident_id = %s "
                "ORDER BY evidence_id", (incident_id,)):
            decisive.append(row[0])
        decoys = rows_as_dicts(connection.execute(
            "SELECT evidence_id, decoy_type FROM truth.decoys WHERE incident_id = %s "
            "ORDER BY evidence_id", (incident_id,)))
        scenario["decisive_evidence"] = decisive
        scenario["decoys"] = decoys
        # Plain-language versions, for the "actual reason" panel.
        scenario["decisive_described"] = []
        for evidence_id in decisive:
            scenario["decisive_described"].append(
                {"evidence_id": evidence_id, "text": describe_evidence(connection, evidence_id)})
        for decoy in decoys:
            decoy["text"] = describe_evidence(connection, decoy["evidence_id"])
        return scenario

    def runs_for(self, connection, incident_id, label=None):
        label = label or self.label
        runs = {}
        rows = rows_as_dicts(connection.execute(
            """
            SELECT DISTINCT ON (r.system)
                   r.run_id::text AS run_id, r.system, r.status, r.started_at, r.ended_at,
                   r.rounds, r.config, r.stats, r.final_report,
                   s.root_cause_correct, s.outcome_correct, s.decisive_recall, s.decoy_hit,
                   s.confidence, s.tool_call_count, s.llm_call_count, s.input_tokens,
                   s.output_tokens, s.latency_ms
              FROM eval.runs r
              LEFT JOIN eval.scores s ON s.run_id = r.run_id
             WHERE r.incident_id = %s AND r.config->>'label' = %s
               AND r.config->>'backend' = ANY(%s) AND r.final_report IS NOT NULL
             ORDER BY r.system, r.started_at DESC
            """, (incident_id, label, BACKENDS)))
        for row in rows:
            started = row["started_at"]
            llm_calls = []
            for call in rows_as_dicts(connection.execute(
                    "SELECT agent, attempt, status, input_tokens, output_tokens, latency_ms, "
                    "created_at FROM eval.llm_calls WHERE run_id = %s ORDER BY call_id",
                    (row["run_id"],))):
                call["end_s"] = (call["created_at"] - started).total_seconds()
                del call["created_at"]
                llm_calls.append(call)
            tool_calls = []
            for call in rows_as_dicts(connection.execute(
                    "SELECT tool, args, attempt, status, latency_ms, evidence_ids, created_at "
                    "FROM eval.tool_calls WHERE run_id = %s ORDER BY call_id",
                    (row["run_id"],))):
                call["end_s"] = (call["created_at"] - started).total_seconds()
                call["evidence_count"] = len(call["evidence_ids"] or [])
                # a few returned IDs, so the tools section can show a real output example
                call["sample_ids"] = list((call["evidence_ids"] or [])[:2])
                del call["created_at"]
                del call["evidence_ids"]
                tool_calls.append(call)

            run = {
                "run_id": row["run_id"], "system": row["system"], "status": row["status"],
                "rounds": row["rounds"], "stats": row["stats"],
                "limits": run_limits(row["config"]), "report": row["final_report"],
                "score": {"correct": row["root_cause_correct"],
                          "outcome_correct": row["outcome_correct"],
                          "decisive_recall": row["decisive_recall"],
                          "decoy_hit": row["decoy_hit"], "confidence": row["confidence"],
                          "tool_calls": row["tool_call_count"],
                          "llm_calls": row["llm_call_count"],
                          "input_tokens": row["input_tokens"],
                          "output_tokens": row["output_tokens"],
                          "latency_s": None if row["latency_ms"] is None
                          else row["latency_ms"] / 1000.0},
                "llm_calls": llm_calls, "tool_calls": tool_calls}
            # The evidence the system's top answer rests on, in plain language.
            run["cited"] = []
            top = top_hypothesis(row["final_report"])
            if top:
                for evidence_id in (top.get("supporting_evidence_ids") or [])[:5]:
                    run["cited"].append({"evidence_id": evidence_id,
                                         "text": describe_evidence(connection, evidence_id)})
            if row["system"] == "multi_agent":
                run["blackboard"] = read_blackboard(
                    self.runs_dir / row["run_id"] / "blackboard.sqlite")
                run["tool_rounds"] = tool_calls_by_round(llm_calls, tool_calls)
            else:
                run["steps"] = split_into_steps(llm_calls, tool_calls)
            runs[row["system"]] = run
        return runs

    def exchanges(self, incident_id, system, run_label=None):
        """Every prompt and reply of one run, in order: what each agent was asked and said.

        New runs keep them in runs/<run_id>/llm_transcript.jsonl. Older runs did not, so their
        exchanges are recovered from the answer cache: the cache files written while that run
        was going (runs were executed one at a time) whose prompt is about this incident."""
        label = run_label or self.label
        connection = self.connect()
        try:
            rows = rows_as_dicts(connection.execute(
                """
                SELECT r.run_id::text AS run_id, r.started_at, r.ended_at, i.summary
                  FROM eval.runs r JOIN evidence.incidents i ON i.incident_id = r.incident_id
                 WHERE r.incident_id = %s AND r.system = %s AND r.config->>'label' = %s
                   AND r.final_report IS NOT NULL
                 ORDER BY r.started_at DESC LIMIT 1
                """, (incident_id, system, label)))
        finally:
            connection.close()
        if not rows:
            return {"source": "none", "exchanges": []}
        run = rows[0]
        transcript = self.runs_dir / run["run_id"] / "llm_transcript.jsonl"
        if transcript.exists():
            entries = []
            for line in transcript.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entries.append(json.loads(line))
            return {"source": "transcript", "exchanges": shape_exchanges(entries)}
        entries = recover_from_cache(self.cache_dir, system, run["summary"],
                                     run["started_at"].timestamp(), run["ended_at"].timestamp())
        return {"source": "cache", "exchanges": shape_exchanges(entries)}

    def conversation(self, run_id):
        """The history of one run, finished or still running. A team run keeps it on its
        blackboard; the single agent has no blackboard, so it has a file of its own."""
        run_dir = self.runs_dir / run_id
        path = run_dir / "blackboard.sqlite"
        if not path.exists():
            path = run_dir / "history.sqlite"
        return {"history": read_conversation(path)}

    def tool_data(self, incident_id, tool):
        """Everything one tool can return for this incident (the raw evidence, as stored).

        This is what an agent could see with that tool if it asked for everything and the tool
        did not fail; the answer key is not part of it."""
        queries = {
            "query_logs": """
                SELECT l.evidence_id, l.ts, s.name AS service, l.instance, l.level, l.message
                  FROM evidence.log_entries l JOIN evidence.services s ON s.service_id = l.service_id
                 WHERE l.incident_id = %s ORDER BY l.ts, l.evidence_id COLLATE "C"
            """,
            "query_metrics": """
                SELECT m.evidence_id, s.name AS service, m.instance, m.metric_name, m.unit,
                       m.source, count(p.ts) AS points, min(p.value) AS lowest, max(p.value) AS highest
                  FROM evidence.metric_series m
                  JOIN evidence.services s ON s.service_id = m.service_id
                  LEFT JOIN evidence.metric_points p ON p.series_id = m.evidence_id
                 WHERE m.incident_id = %s
                 GROUP BY m.evidence_id, s.name, m.instance, m.metric_name, m.unit, m.source
                 ORDER BY s.name COLLATE "C", m.metric_name COLLATE "C", m.instance NULLS FIRST
            """,
            "get_change_history": """
                SELECT c.evidence_id, c.ts, s.name AS service, c.change_type, c.version, c.author,
                       c.summary, d.diff
                  FROM evidence.changes c JOIN evidence.services s ON s.service_id = c.service_id
                  LEFT JOIN evidence.change_details d ON d.evidence_id = c.evidence_id
                 WHERE c.incident_id = %s ORDER BY c.ts
            """,
            "get_service_dependencies": """
                SELECT d.evidence_id, a.name AS caller, b.name AS callee, d.config
                  FROM evidence.service_dependencies d
                  JOIN evidence.services a ON a.service_id = d.caller_id
                  JOIN evidence.services b ON b.service_id = d.callee_id
                 WHERE d.incident_id = %s ORDER BY a.name COLLATE "C", b.name COLLATE "C"
            """,
            "search_past_incidents": """
                SELECT evidence_id, occurred_at, title, root_cause_category, root_cause_component,
                       resolution, symptoms
                  FROM evidence.past_incidents WHERE incident_id = %s ORDER BY occurred_at
            """,
        }
        if tool not in queries:
            return None
        connection = self.connect()
        try:
            rows = rows_as_dicts(connection.execute(queries[tool], (incident_id,)))
        finally:
            connection.close()
        for row in rows:
            for key in ["ts", "occurred_at"]:
                if key in row:
                    row[key] = epoch_ms(row[key])
        return {"tool": tool, "rows": rows}

    def series_points(self, incident_id, evidence_id):
        connection = self.connect()
        try:
            found = rows_as_dicts(connection.execute(
                """
                SELECT m.evidence_id, s.name AS service, m.instance, m.metric_name, m.unit
                  FROM evidence.metric_series m
                  JOIN evidence.services s ON s.service_id = m.service_id
                 WHERE m.incident_id = %s AND m.evidence_id = %s
                """, (incident_id, evidence_id)))
            if not found:
                return None
            points = []
            for row in connection.execute(
                    "SELECT ts, value FROM evidence.metric_points WHERE incident_id = %s "
                    "AND series_id = %s ORDER BY ts", (incident_id, evidence_id)):
                points.append([epoch_ms(row[0]), row[1]])
        finally:
            connection.close()
        series = found[0]
        series["points"] = points
        return series
