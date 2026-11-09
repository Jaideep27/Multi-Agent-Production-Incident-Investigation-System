"""PostgresBackend: the five tools as SQL over the evidence schema, as agent_ro.

Every query runs in its own transaction on a pooled connection, and the transaction is bound
to the incident with set_config(..., true). The binding ends with the transaction, so a
connection handed back to the pool carries nothing over. Row-level security does the
filtering; the queries also name the incident explicitly, as a second guard.

Text ORDER BY uses COLLATE "C" so the order is the same on any database locale.
"""

import base64
import json
import re
from datetime import datetime, timezone

from psycopg_pool import AsyncConnectionPool

from investigator import db
from investigator.models.tools import ToolItem
from investigator.tools.backend import BackendError, BackendPage, EvidenceBackend

# Words too common to rank past incidents by.
STOPWORDS = ["the", "and", "for", "with", "from", "after", "during", "under", "into", "was",
             "were", "are", "not", "but", "has", "have", "had", "our", "all", "any"]


async def open_agent_pool(min_size=1, max_size=4):
    """An opened pool of agent_ro connections (credentials from the environment)."""
    pool = AsyncConnectionPool(conninfo=db.conninfo_for("agent_ro"), min_size=min_size,
                               max_size=max_size, open=False)
    await pool.open(wait=True)
    return pool


def iso(ts):
    """UTC timestamp as text ending in Z, the same form the Pydantic models use."""
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def like_pattern(text):
    """A LIKE pattern matching text literally anywhere ('%' and '_' lose their meaning)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


def encode_cursor(ts, evidence_id):
    raw = json.dumps([iso(ts), evidence_id]).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor):
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        ts_text, evidence_id = json.loads(raw.decode("utf-8"))
        ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
    except Exception:
        raise BackendError("invalid cursor: pass back next_cursor from the previous result")
    if not isinstance(evidence_id, str):
        raise BackendError("invalid cursor: pass back next_cursor from the previous result")
    return ts, evidence_id


def query_words(text):
    words = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if len(word) < 2 or word in STOPWORDS:
            continue
        if word not in words:
            words.append(word)
    return words


def metric_stats(values):
    if not values:
        return None
    total = 0.0
    for value in values:
        total = total + value
    return {
        "min": min(values),
        "max": max(values),
        "mean": round(total / len(values), 2),
        "first": values[0],
        "last": values[-1],
    }


class PostgresBackend(EvidenceBackend):
    def __init__(self, pool, incident_id):
        self.pool = pool
        self.incident_id = incident_id
        self.service_names = None    # loaded once; an incident's services never change

    async def fetch(self, sql, params):
        """Run one query in a transaction bound to this incident and return all rows."""
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute("SELECT set_config('app.incident_id', %s, true)",
                                         (self.incident_id,))
                cursor = await connection.execute(sql, params)
                return await cursor.fetchall()

    async def known_services(self):
        if self.service_names is None:
            rows = await self.fetch(
                'SELECT name FROM evidence.services WHERE incident_id = %s ORDER BY name COLLATE "C"',
                (self.incident_id,),
            )
            names = []
            for row in rows:
                names.append(row[0])
            self.service_names = names
        return self.service_names

    async def unknown_service_page(self, service):
        """None if the service exists, else an empty page that lists the real names."""
        if service is None:
            return None
        names = await self.known_services()
        if service in names:
            return None
        warning = "unknown service '%s'; known services: %s" % (service, ", ".join(names))
        return BackendPage(items=[], warnings=[warning])

    async def metric_names(self, service):
        """Distinct metric names, for one service or all; lets a wrong guess be corrected."""
        rows = await self.fetch(
            """
            SELECT DISTINCT m.metric_name COLLATE "C" AS name
              FROM evidence.metric_series m
              JOIN evidence.services s
                ON s.incident_id = m.incident_id AND s.service_id = m.service_id
             WHERE m.incident_id = %(incident_id)s
               AND (%(service)s::text IS NULL OR s.name = %(service)s)
             ORDER BY name
            """,
            {"incident_id": self.incident_id, "service": service},
        )
        names = []
        for row in rows:
            names.append(row[0])
        return names

    async def check_incident_exists(self):
        rows = await self.fetch("SELECT 1 FROM evidence.incidents WHERE incident_id = %s",
                                (self.incident_id,))
        if not rows:
            raise LookupError("incident %s does not exist or is not visible" % self.incident_id)

    async def failure_rules(self):
        rows = await self.fetch(
            'SELECT tool_name, rules FROM evidence.tool_behavior WHERE incident_id = %s '
            'ORDER BY tool_name COLLATE "C"',
            (self.incident_id,),
        )
        rules = []
        for tool_name, rule in rows:
            rules.append([tool_name, rule])
        return rules

    # ------------------------------------------------------------------ query_logs

    async def query_logs(self, args):
        unknown = await self.unknown_service_page(args.service)
        if unknown is not None:
            return unknown

        after_ts = None
        after_id = None
        if args.cursor is not None:
            after_ts, after_id = decode_cursor(args.cursor)

        pattern = None
        if args.contains is not None:
            pattern = like_pattern(args.contains)

        rows = await self.fetch(
            r"""
            SELECT l.evidence_id, s.name, l.instance, l.ts, l.level, l.message, l.attrs
              FROM evidence.log_entries l
              JOIN evidence.services s
                ON s.incident_id = l.incident_id AND s.service_id = l.service_id
             WHERE l.incident_id = %(incident_id)s
               AND (%(service)s::text IS NULL OR s.name = %(service)s)
               AND (cardinality(%(levels)s::text[]) = 0 OR l.level = ANY(%(levels)s::text[]))
               AND (%(pattern)s::text IS NULL OR l.message ILIKE %(pattern)s ESCAPE '\')
               AND (%(start)s::timestamptz IS NULL OR l.ts >= %(start)s)
               AND (%(end)s::timestamptz IS NULL OR l.ts < %(end)s)
               AND (%(after_ts)s::timestamptz IS NULL
                    OR l.ts > %(after_ts)s
                    OR (l.ts = %(after_ts)s AND l.evidence_id COLLATE "C" > %(after_id)s))
             ORDER BY l.ts, l.evidence_id COLLATE "C"
             LIMIT %(fetch)s
            """,
            {
                "incident_id": self.incident_id,
                "service": args.service,
                "levels": list(args.levels),
                "pattern": pattern,
                "start": args.start,
                "end": args.end,
                "after_ts": after_ts,
                "after_id": after_id,
                "fetch": args.limit + 1,
            },
        )

        truncated = len(rows) > args.limit
        rows = rows[:args.limit]
        items = []
        for evidence_id, service, instance, ts, level, message, attrs in rows:
            items.append(ToolItem(evidence_id=evidence_id, data={
                "service": service, "instance": instance, "ts": iso(ts), "level": level,
                "message": message, "attrs": attrs,
            }))

        next_cursor = None
        if truncated:
            last = rows[-1]
            next_cursor = encode_cursor(last[3], last[0])
        return BackendPage(items=items, truncated=truncated, next_cursor=next_cursor)

    # ------------------------------------------------------------------ query_metrics

    async def query_metrics(self, args):
        unknown = await self.unknown_service_page(args.service)
        if unknown is not None:
            return unknown

        series_rows = await self.fetch(
            """
            SELECT m.evidence_id, s.name, m.instance, m.metric_name, m.unit, m.source, m.interval_s
              FROM evidence.metric_series m
              JOIN evidence.services s
                ON s.incident_id = m.incident_id AND s.service_id = m.service_id
             WHERE m.incident_id = %(incident_id)s
               AND (%(service)s::text IS NULL OR s.name = %(service)s)
               AND (%(metric_name)s::text IS NULL OR m.metric_name = %(metric_name)s)
               AND (%(instance)s::text IS NULL OR m.instance = %(instance)s)
             ORDER BY s.name COLLATE "C", m.metric_name COLLATE "C",
                      m.instance COLLATE "C" NULLS FIRST, m.evidence_id COLLATE "C"
             LIMIT %(fetch)s
            """,
            {
                "incident_id": self.incident_id,
                "service": args.service,
                "metric_name": args.metric_name,
                "instance": args.instance,
                "fetch": args.limit + 1,
            },
        )
        truncated = len(series_rows) > args.limit
        series_rows = series_rows[:args.limit]
        if not series_rows:
            warnings = []
            if args.metric_name is not None:
                names = await self.metric_names(args.service)
                if args.metric_name not in names:
                    scope = "in this incident"
                    if args.service is not None:
                        scope = "for " + args.service
                    warnings.append("no metric named '%s' %s; available metrics: %s"
                                    % (args.metric_name, scope, ", ".join(names)))
            return BackendPage(items=[], warnings=warnings)

        series_ids = []
        for row in series_rows:
            series_ids.append(row[0])
        point_rows = await self.fetch(
            """
            SELECT series_id, ts, value
              FROM evidence.metric_points
             WHERE incident_id = %(incident_id)s
               AND series_id = ANY(%(series_ids)s::text[])
               AND (%(start)s::timestamptz IS NULL OR ts >= %(start)s)
               AND (%(end)s::timestamptz IS NULL OR ts < %(end)s)
             ORDER BY series_id COLLATE "C", ts
            """,
            {"incident_id": self.incident_id, "series_ids": series_ids,
             "start": args.start, "end": args.end},
        )
        points_by_series = {}
        for series_id, ts, value in point_rows:
            if series_id not in points_by_series:
                points_by_series[series_id] = []
            points_by_series[series_id].append([iso(ts), value])

        items = []
        for evidence_id, service, instance, metric_name, unit, source, interval_s in series_rows:
            points = points_by_series.get(evidence_id, [])
            values = []
            for ts_text, value in points:
                values.append(value)
            items.append(ToolItem(evidence_id=evidence_id, data={
                "service": service, "instance": instance, "metric_name": metric_name,
                "unit": unit, "source": source, "interval_s": interval_s,
                "stats": metric_stats(values), "points": points,
            }))
        return BackendPage(items=items, truncated=truncated)

    # ------------------------------------------------------------------ get_change_history

    async def get_change_history(self, args):
        unknown = await self.unknown_service_page(args.service)
        if unknown is not None:
            return unknown

        rows = await self.fetch(
            """
            SELECT c.evidence_id, s.name, c.ts, c.change_type, c.version, c.author, c.summary,
                   d.diff
              FROM evidence.changes c
              JOIN evidence.services s
                ON s.incident_id = c.incident_id AND s.service_id = c.service_id
              LEFT JOIN evidence.change_details d
                ON d.incident_id = c.incident_id AND d.evidence_id = c.evidence_id
             WHERE c.incident_id = %(incident_id)s
               AND (%(service)s::text IS NULL OR s.name = %(service)s)
               AND (%(change_type)s::text IS NULL OR c.change_type = %(change_type)s)
               AND (cardinality(%(change_ids)s::text[]) = 0
                    OR c.evidence_id = ANY(%(change_ids)s::text[]))
               AND (%(start)s::timestamptz IS NULL OR c.ts >= %(start)s)
               AND (%(end)s::timestamptz IS NULL OR c.ts < %(end)s)
             ORDER BY c.ts, c.evidence_id COLLATE "C"
             LIMIT %(fetch)s
            """,
            {
                "incident_id": self.incident_id,
                "service": args.service,
                "change_type": args.change_type,
                "change_ids": list(args.change_ids),
                "start": args.start,
                "end": args.end,
                "fetch": args.limit + 1,
            },
        )
        truncated = len(rows) > args.limit
        rows = rows[:args.limit]
        items = []
        for evidence_id, service, ts, change_type, version, author, summary, diff in rows:
            data = {"service": service, "ts": iso(ts), "change_type": change_type,
                    "version": version, "author": author, "summary": summary}
            if args.include_diff:
                data["diff"] = diff
            items.append(ToolItem(evidence_id=evidence_id, data=data))
        return BackendPage(items=items, truncated=truncated)

    # ------------------------------------------------------------------ get_service_dependencies

    async def get_service_dependencies(self, args):
        unknown = await self.unknown_service_page(args.service)
        if unknown is not None:
            return unknown

        # downstream: edges where the service is the caller (what it calls)
        # upstream:   edges where the service is the callee (who calls it)
        want_downstream = args.direction == "downstream" or args.direction == "both"
        want_upstream = args.direction == "upstream" or args.direction == "both"
        rows = await self.fetch(
            """
            SELECT d.evidence_id, caller.name, callee.name, d.config
              FROM evidence.service_dependencies d
              JOIN evidence.services caller
                ON caller.incident_id = d.incident_id AND caller.service_id = d.caller_id
              JOIN evidence.services callee
                ON callee.incident_id = d.incident_id AND callee.service_id = d.callee_id
             WHERE d.incident_id = %(incident_id)s
               AND (%(service)s::text IS NULL
                    OR (%(downstream)s AND caller.name = %(service)s)
                    OR (%(upstream)s AND callee.name = %(service)s))
             ORDER BY d.evidence_id COLLATE "C"
            """,
            {"incident_id": self.incident_id, "service": args.service,
             "downstream": want_downstream, "upstream": want_upstream},
        )
        items = []
        for evidence_id, caller, callee, config in rows:
            items.append(ToolItem(evidence_id=evidence_id,
                                  data={"caller": caller, "callee": callee, "config": config}))
        return BackendPage(items=items)

    # ------------------------------------------------------------------ search_past_incidents

    async def search_past_incidents(self, args):
        rows = await self.fetch(
            """
            SELECT evidence_id, occurred_at, title, root_cause_category, root_cause_component,
                   resolution, symptoms
              FROM evidence.past_incidents
             WHERE incident_id = %(incident_id)s
               AND (%(category)s::text IS NULL OR root_cause_category = %(category)s)
             ORDER BY evidence_id COLLATE "C"
            """,
            {"incident_id": self.incident_id, "category": args.category},
        )

        words = query_words(args.query)
        scored = []
        for evidence_id, occurred_at, title, category, component, resolution, symptoms in rows:
            text_parts = [title, resolution or "", component or ""]
            for symptom in symptoms:
                text_parts.append(symptom)
            row_words = query_words(" ".join(text_parts))
            score = 0
            for word in words:
                if word in row_words:
                    score = score + 1
            if score == 0:
                continue
            scored.append({
                "evidence_id": evidence_id,
                "score": score,
                "occurred_at": occurred_at,
                "data": {
                    "occurred_at": iso(occurred_at), "title": title,
                    "root_cause_category": category, "root_cause_component": component,
                    "resolution": resolution, "symptoms": symptoms, "match_score": score,
                },
            })

        # best score first, then most recent, then ID (stable order for ties)
        scored.sort(key=search_sort_key)
        truncated = len(scored) > args.limit
        items = []
        for entry in scored[:args.limit]:
            items.append(ToolItem(evidence_id=entry["evidence_id"], data=entry["data"]))
        return BackendPage(items=items, truncated=truncated)


def search_sort_key(entry):
    return (-entry["score"], -entry["occurred_at"].timestamp(), entry["evidence_id"])
